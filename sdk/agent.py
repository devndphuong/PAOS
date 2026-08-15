"""SDK cho người viết Agent (doc 04 §3, doc 12 §3) — lát cắt 5a (doc 19 P-M0-5),
mở rộng đầy đủ ở P-M3-1 (doc 19).

Độc lập hoàn toàn với kernel/ — cùng lý do đã áp dụng cho sdk/provider.py:
agents/ bị cấm import kernel/ kể cả GIÁN TIẾP qua sdk/ (.importlinter
"agent-chi-dung-sdk" kiểm đường phụ thuộc bắc cầu). Việc nối AgentContext
với StateStore thật là trách nhiệm của apps/ (tầng cao nhất, được phép
import mọi tầng) — AgentContext chỉ nhận các callback đã cấu hình sẵn
(`persist_artifact`, `report_progress`, `write_checkpoint`...), không tự
import kernel.state.db.

ID artifact dùng thẳng thư viện `ulid` (không qua kernel/ids.py — bản đó có
thêm StrictMonotonicPolicy cho event_seq, không cần thiết ở đây). Timestamp
dùng datetime.now(UTC) trực tiếp, không qua kernel/clock.py — artifact
không nằm trên đường quyết định tính-tất-định như Event (doc 08 §7.3), nên
không cần injectable clock ở M0.

P-M3-1: manifest thêm resources/quality_rubric/max_retries/timeout_sec/
checkpointable/listens (doc 04 §3.2, "chừa cột" — cùng tiền lệ đã áp dụng
cho ProviderManifest.resources ở M0, Router mới thật sự ĐỌC resources ở
M2-3). Protocol thêm resume() (bắt buộc có mặt, chỉ agent có checkpointable:
true mới thực sự được Runner gọi tới — agent không checkpoint được có thể
raise AgentError NOT_IMPLEMENTED trong thân resume(), xem SummarizeAgent).
AgentContext thêm progress()/checkpoint() — 2 callback mới có giá trị mặc
định no-op để không phá vỡ chỗ khởi tạo AgentContext trực tiếp trong test
cũ (tests/sdk, tests/agents/summarize) chưa quan tâm tới progress/checkpoint.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import ulid
import yaml

from sdk.provider import ErrorCode

_PROGRESS_MIN = 0.0
_PROGRESS_MAX = 100.0


@dataclass(frozen=True)
class Artifact:
    """doc 03 §2.4 — bất biến, sửa = tạo bản mới có supersedes (ADR-0013)."""

    artifact_id: str
    process_id: str
    task_id: str | None
    type: str
    path: str
    mime: str
    sha256: str
    bytes: int
    produced_by: dict[str, str]
    supersedes: str | None
    created_at: str


class AgentError(Exception):
    """Lỗi phía Agent SDK — độc lập PaosError, cùng lý do ProviderError (xem module docstring).
    Dùng chung ErrorCode với sdk/provider.py — cùng gói sdk/, không phạm ranh giới."""

    def __init__(self, code: ErrorCode, message: str, *, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@dataclass(frozen=True)
class AgentManifest:
    """doc 04 §3.2, đầy đủ từ P-M3-1. resources/max_retries/timeout_sec đã
    khai báo nhưng CHƯA có caller enforce ở lát này (Runner hiện không đọc
    timeout_sec, Scheduler hiện không giữ resources riêng cho Agent — chỉ
    Provider/Router mới giữ resource token, doc 19 P-M1-3a) — "chừa cột,
    đừng chừa code" (doc 18 §10), cùng tiền lệ ProviderManifest.resources.
    checkpointable=True là điều kiện DUY NHẤT để Runner gọi resume() thay vì
    chạy lại initialize()..execute() từ đầu (doc 04 §3.1)."""

    agent_id: str
    version: int
    needs: list[str]
    produces: list[str]
    capabilities: list[str]  # AgentContext.call() cưỡng chế danh sách này — xem docstring class
    emits: list[str]
    listens: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    quality_rubric: str | None = None
    max_retries: int = 3
    timeout_sec: int = 600
    checkpointable: bool = False


def load_agent_manifest(path: Path) -> AgentManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return AgentManifest(
        agent_id=data["id"],
        version=data["version"],
        needs=data.get("needs", []),
        produces=data.get("produces", []),
        capabilities=data.get("capabilities", []),
        emits=data.get("emits", []),
        listens=data.get("listens", []),
        resources=data.get("resources", []),
        quality_rubric=data.get("quality_rubric"),
        max_retries=data.get("max_retries", 3),
        timeout_sec=data.get("timeout_sec", 600),
        checkpointable=data.get("checkpointable", False),
    )


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class Plan:
    prompt: str
    task_class: str | None = None


@dataclass(frozen=True)
class ExecResult:
    data: dict[str, Any]


@dataclass(frozen=True)
class ReviewResult:
    passed: bool
    reason: str | None = None


class Agent(Protocol):
    """doc 04 §3.1 — 6 bước bắt buộc + resume() (P-M3-1). resume() chỉ được
    Runner gọi khi `manifest.checkpointable` là True VÀ có checkpoint từ lần
    chạy trước (process crash giữa RUNNING, restart) — agent không checkpoint
    được (mặc định) không cần resume() thật, có thể raise ngay (xem
    SummarizeAgent.resume)."""

    manifest: AgentManifest

    async def initialize(self, ctx: AgentContext) -> None: ...
    async def validate(self, inputs: dict[str, Any]) -> ValidationResult: ...
    async def think(self, inputs: dict[str, Any]) -> Plan: ...
    async def execute(self, plan: Plan) -> ExecResult: ...
    async def review(self, result: ExecResult) -> ReviewResult: ...
    async def publish(self, result: ExecResult) -> list[Artifact]: ...
    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult: ...


PersistArtifact = Callable[[Artifact], Awaitable[None]]
CallCapability = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
EmitEvent = Callable[[str, dict[str, Any]], Awaitable[None]]
ReportProgress = Callable[[float, str], Awaitable[None]]
WriteCheckpoint = Callable[[dict[str, Any]], Awaitable[int]]


async def _noop_report_progress(pct: float, message: str) -> None:
    pass


async def _noop_write_checkpoint(state: dict[str, Any]) -> int:
    return 0


class AgentContext:
    """`call()` cưỡng chế theo `manifest.capabilities` — nhưng ở M0 đây là cưỡng
    chế PHÍA SDK, chưa phải Kernel: chưa có Capability Router (M2) làm cổng
    chặn không thể vượt qua. Đủ dùng cho M0, KHÔNG chống được agent cố tình đi
    vòng qua AgentContext. Cưỡng chế thật (không thể lách) là ở M2.
    """

    def __init__(
        self,
        *,
        process_id: str,
        task_id: str | None,
        workspace_dir: Path,
        agent_id: str,
        prompts_dir: Path,
        manifest: AgentManifest,
        persist_artifact: PersistArtifact,
        call_capability: CallCapability,
        emit_event: EmitEvent,
        report_progress: ReportProgress = _noop_report_progress,
        write_checkpoint: WriteCheckpoint = _noop_write_checkpoint,
    ) -> None:
        self._process_id = process_id
        self._task_id = task_id
        self._workspace_dir = workspace_dir
        self._agent_id = agent_id
        self._prompts_dir = prompts_dir
        self._manifest = manifest
        self._persist_artifact = persist_artifact
        self._call_capability = call_capability
        self._emit_event = emit_event
        self._report_progress = report_progress
        self._write_checkpoint = write_checkpoint

    async def call(self, capability_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
        if capability_ref not in self._manifest.capabilities:
            raise AgentError(
                ErrorCode.PERMISSION_DENIED,
                f"Agent {self._manifest.agent_id} không khai báo capability {capability_ref}",
                hint=f"Thêm '{capability_ref}' vào capabilities trong manifest.yaml của agent",
            )
        return await self._call_capability(capability_ref, payload)

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type not in self._manifest.emits:
            raise AgentError(
                ErrorCode.PERMISSION_DENIED,
                f"Agent {self._manifest.agent_id} không khai báo emit {event_type}",
                hint=f"Thêm '{event_type}' vào emits trong manifest.yaml của agent",
            )
        await self._emit_event(event_type, payload)

    async def progress(self, pct: float, message: str = "") -> None:
        """doc 04 §3.3 quy tắc 4 — bắt buộc gọi nếu agent chạy > 30s. Runner nối
        callback này tới `ProcessManager.report_progress()` (throttle ≤1
        event/giây, doc 05 §3.1) — bản thân AgentContext không throttle, chỉ
        chuyển tiếp, tránh trùng logic 2 nơi."""
        if not _PROGRESS_MIN <= pct <= _PROGRESS_MAX:
            raise AgentError(
                ErrorCode.INVALID_INPUT,
                f"progress {pct} ngoài khoảng [0, 100]",
                hint="Gọi ctx.progress(pct, message) với pct trong khoảng 0-100",
            )
        await self._report_progress(pct, message)

    async def checkpoint(self, state: dict[str, Any]) -> int:
        """Ghi trạng thái nội bộ để `resume()` đọc lại sau crash — chỉ có ý
        nghĩa khi `manifest.checkpointable` là True (doc 04 §3.1/§3.2)."""
        return await self._write_checkpoint(state)

    def prompt(self, version: str) -> str:
        """Đọc agents/<agent_id>/prompts/<version>.md — không nhúng prompt trong code."""
        path = self._prompts_dir / f"{version}.md"
        if not path.is_file():
            raise AgentError(
                ErrorCode.NOT_FOUND,
                f"Không có prompt '{version}' cho agent {self._agent_id}",
                hint=f"Tạo file {path} trước khi gọi ctx.prompt('{version}')",
            )
        return path.read_text(encoding="utf-8")

    async def write_artifact(
        self, type: str, filename: str, content: str, *, mime: str = "text/plain"
    ) -> Artifact:
        """Ghi artifact vào workspace/artifacts/<process_id>/ — quy ước tạm cho M0
        (chưa có Project thật tới M3/M6, xem docs/backlog.md)."""
        artifacts_root = (self._workspace_dir / "artifacts" / self._process_id).resolve()
        target = (artifacts_root / filename).resolve()
        if target != artifacts_root and artifacts_root not in target.parents:
            raise AgentError(
                ErrorCode.INVALID_INPUT,
                f"Đường dẫn artifact '{filename}' nằm ngoài phạm vi cho phép",
                hint="Không dùng '..' hay đường dẫn tuyệt đối trong tên file artifact",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        target.write_bytes(data)

        artifact = Artifact(
            artifact_id=f"art_{ulid.ULID()}",
            process_id=self._process_id,
            task_id=self._task_id,
            type=type,
            path=str(target.relative_to(self._workspace_dir)),
            mime=mime,
            sha256=hashlib.sha256(data).hexdigest(),
            bytes=len(data),
            produced_by={"agent": self._agent_id},
            supersedes=None,
            created_at=datetime.now(UTC).isoformat(),
        )
        await self._persist_artifact(artifact)
        return artifact
