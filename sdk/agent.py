"""SDK cho người viết Agent (doc 04 §3, doc 12 §3) — lát cắt 5a, doc 19 P-M0-5.

Độc lập hoàn toàn với kernel/ — cùng lý do đã áp dụng cho sdk/provider.py:
agents/ bị cấm import kernel/ kể cả GIÁN TIẾP qua sdk/ (.importlinter
"agent-chi-dung-sdk" kiểm đường phụ thuộc bắc cầu). Việc nối AgentContext
với StateStore thật là trách nhiệm của apps/ (tầng cao nhất, được phép
import mọi tầng) — AgentContext chỉ nhận một callback đã cấu hình sẵn
(`persist_artifact`), không tự import kernel.state.db.

ID artifact dùng thẳng thư viện `ulid` (không qua kernel/ids.py — bản đó có
thêm StrictMonotonicPolicy cho event_seq, không cần thiết ở đây). Timestamp
dùng datetime.now(UTC) trực tiếp, không qua kernel/clock.py — artifact
không nằm trên đường quyết định tính-tất-định như Event (doc 08 §7.3), nên
không cần injectable clock ở M0.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import ulid

from sdk.provider import ErrorCode


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


PersistArtifact = Callable[[Artifact], Awaitable[None]]


class AgentContext:
    """Phần plumbing của lát cắt 5a: đọc prompt có version, ghi artifact an toàn.
    `call()` (gọi Capability, cưỡng chế theo manifest) thuộc 5b."""

    def __init__(
        self,
        *,
        process_id: str,
        task_id: str | None,
        workspace_dir: Path,
        agent_id: str,
        prompts_dir: Path,
        persist_artifact: PersistArtifact,
    ) -> None:
        self._process_id = process_id
        self._task_id = task_id
        self._workspace_dir = workspace_dir
        self._agent_id = agent_id
        self._prompts_dir = prompts_dir
        self._persist_artifact = persist_artifact

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
