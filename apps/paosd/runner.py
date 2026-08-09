"""Bộ chạy Process — KHÔNG phải DAG Scheduler thật (doc 19 P-M1-2, trả nợ RSK-21).

M0 (lát 5c): `on_process_created` chạy TOÀN BỘ agent đồng bộ trong dispatch(),
khiến `POST /v1/jobs` block tới khi agent xong. M1-2 tách "đưa vào hàng đợi"
(nhanh, vẫn đồng bộ trong dispatch — chỉ 2 lần ghi DB) khỏi "thực thi" (chạy
nền qua `worker_loop()`, không còn nằm trên đường HTTP request/response).

Vẫn 1 worker, 1 job một lúc — concurrency thật (nhiều worker + resource token)
là M1-3. Đây KHÔNG phải backpressure có ngưỡng: hàng đợi không giới hạn, job
mới tự "chờ trong QUEUED" nếu worker đang bận việc khác — đủ cho phạm vi
M1-2 (doc 18 §10 áp dụng ở mức cơ chế, không phải chỉ schema).

Hệ quả cho `POST /v1/jobs` (ADR-0026, doc 15): response giờ nghĩa là "đã tạo
và đưa vào hàng đợi", KHÔNG còn nghĩa "đã chạy xong". Client phải poll
`GET /v1/processes/{pid}` hoặc theo dõi `events tail`/`explain`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from agents.summarize.agent import SummarizeAgent
from kernel import ids
from kernel.errors import ErrorCode as KernelErrorCode
from kernel.errors import PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.process.manager import ProcessManager, ProcessState
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub.adapter import StubAdapter
from sdk.agent import Agent, AgentContext, AgentError, Artifact, CallCapability, EmitEvent
from sdk.provider import CallContext, ProviderAdapter, ProviderError
from sdk.provider import ErrorCode as SdkErrorCode

_REPO_ROOT = Path(__file__).resolve().parents[2]

# workflow_ref -> (agent, thư mục prompts). M0 chỉ có một agent thật — bảng
# tra cứu tĩnh này là chỗ M1+ sẽ thay bằng nạp động theo manifest thay vì
# hardcode (doc 18 §10).
_AGENTS: dict[str, tuple[Agent, Path]] = {
    "agent:summarize.agent@1": (
        SummarizeAgent(),
        _REPO_ROOT / "agents" / "summarize" / "prompts",
    ),
}

# provider_id -> instance adapter đã khởi tạo. Registry chỉ giữ metadata
# (ProviderManifest), không tự khởi tạo adapter — chưa có cơ chế nạp động
# theo provider_id ở M0. Chọn "provider đầu tiên có adapter nạp sẵn" là đơn
# giản hoá có chủ đích (1 provider thật/capability ở M0) — Provider Ranking
# thật là M2 (doc 13, R27).
_ADAPTERS: dict[str, ProviderAdapter] = {"stub.deterministic": StubAdapter()}


class Runner:
    """Nối Registry + ProcessManager + EventBus + StateStore thật lại với
    nhau để chạy Agent — trách nhiệm của tầng `apps/` (tầng cao nhất, được
    phép import mọi tầng, doc 17 §1)."""

    def __init__(
        self,
        manager: ProcessManager,
        events: EventBus,
        registry: Registry,
        store: StateStore,
        workspace_root: Path,
    ) -> None:
        self._manager = manager
        self._events = events
        self._registry = registry
        self._store = store
        self._workspace_root = workspace_root
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def on_process_created(self, envelope: EventEnvelope) -> None:
        """Chạy đồng bộ trong dispatch() — chỉ 2 lần ghi DB, đủ nhanh để không
        đáng tách. Thực thi Agent thật sự nằm ở worker_loop()/_run_one()."""
        process_id = envelope.process_id
        if process_id is None:
            return  # không nên xảy ra — PROCESS_CREATED luôn có process_id

        # doc 02 §3.1: CREATED -> PLANNING -> QUEUED. PLANNING chưa có logic
        # thật (Workflow YAML engine là M3) — chỉ đi qua để khớp bảng transition
        # đầy đủ (doc 19 P-M1-1).
        await self._manager.transition(process_id, ProcessState.PLANNING)
        await self._manager.transition(process_id, ProcessState.QUEUED)
        await self._queue.put(process_id)

    async def enqueue_existing(self, process_id: str) -> None:
        """Đưa một process đã ở QUEUED từ trước khi daemon khởi động lại vào
        hàng đợi trong bộ nhớ — bù cho việc `asyncio.Queue` không sống sót qua
        restart. Gọi từ `apps/paosd/wiring.py::build_daemon()` (doc 19 P-M1-2).
        Process kẹt ở RUNNING lúc crash KHÔNG được đụng tới ở đây — resume
        thật (từ checkpoint) là M1-4, ngoài phạm vi lát này."""
        await self._queue.put(process_id)

    async def worker_loop(self) -> None:
        """Chạy nền vô hạn tới khi bị cancel lúc daemon tắt (`Daemon.stop()`).
        Một job lỗi không làm chết cả loop — `_run_one()` tự bắt lỗi của nó."""
        while True:
            process_id = await self._queue.get()
            try:
                await self._run_one(process_id)
            finally:
                self._queue.task_done()

    async def _run_one(self, process_id: str) -> None:
        process = await self._manager.get(process_id)
        if process is None:
            return  # không nên xảy ra — process_id luôn tồn tại khi vào hàng đợi

        workflow_ref = process.workflow_ref
        match = _AGENTS.get(workflow_ref)

        await self._manager.transition(process_id, ProcessState.RUNNING)

        if match is None:
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.NOT_FOUND.value,
                error_message=f"Không có agent nào khớp workflow_ref '{workflow_ref}'",
            )
            return

        agent, prompts_dir = match
        spec = await self._load_spec(process_id)
        try:
            await self._run_agent(agent, prompts_dir, process_id, spec)
        except (AgentError, ProviderError, PaosError) as exc:
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=exc.code.value,
                error_message=exc.message,
            )
            return
        except Exception as exc:  # an toàn cuối — process không bao giờ được kẹt ở RUNNING
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.INTERNAL.value,
                error_message=str(exc),
            )
            return

        await self._manager.transition(process_id, ProcessState.SUCCEEDED)

    async def _load_spec(self, process_id: str) -> dict[str, Any]:
        """`spec` job gốc không nằm trên `Process` — đọc lại từ payload event
        `kernel.process.created` (durable) thay vì thêm một cách đọc bảng
        `jobs` riêng chỉ để phục vụ một chỗ dùng (doc 19 P-M1-2)."""
        trace = await self._events.events_for_process(process_id)
        for envelope in trace:
            if envelope.type == EventType.PROCESS_CREATED.value:
                spec: dict[str, Any] = envelope.payload.get("spec", {})
                return spec
        return {}

    async def _run_agent(
        self, agent: Agent, prompts_dir: Path, process_id: str, spec: dict[str, Any]
    ) -> None:
        ctx = AgentContext(
            process_id=process_id,
            task_id=None,
            workspace_dir=self._workspace_root,
            agent_id=agent.manifest.agent_id,
            prompts_dir=prompts_dir,
            manifest=agent.manifest,
            persist_artifact=self._persist_artifact,
            call_capability=self._make_call_capability(process_id),
            emit_event=self._make_emit_event(process_id, agent.manifest.agent_id),
        )
        await agent.initialize(ctx)

        validation = await agent.validate(spec)
        if not validation.ok:
            raise AgentError(
                SdkErrorCode.INVALID_INPUT,
                f"validate() từ chối input: {validation.reason}",
                hint="Kiểm lại spec truyền vào job — xem manifest.needs của agent",
            )

        plan = await agent.think(spec)
        exec_result = await agent.execute(plan)

        review = await agent.review(exec_result)
        if not review.passed:
            raise AgentError(
                SdkErrorCode.QUALITY_BELOW_THRESHOLD,
                f"review() từ chối kết quả: {review.reason}",
                hint="Xem lại prompt hoặc input — kết quả không đạt ngưỡng tối thiểu",
            )

        await agent.publish(exec_result)

    def _make_call_capability(self, process_id: str) -> CallCapability:
        async def call_capability(capability_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
            capability_id, version_str = capability_ref.split("@")
            version = int(version_str)
            providers = self._registry.providers_for(capability_id, version)
            adapter = next(
                (_ADAPTERS[p.provider_id] for p in providers if p.provider_id in _ADAPTERS), None
            )
            if adapter is None:
                raise ProviderError(
                    SdkErrorCode.PROVIDER_DOWN,
                    f"Không có adapter đã nạp cho provider nào phục vụ {capability_ref}",
                    hint="Xem apps/paosd/runner.py::_ADAPTERS — M0 chỉ nạp stub.deterministic",
                )
            call_ctx = CallContext(
                call_id=ids.new_id("call"),
                process_id=process_id,
                task_id=None,
                deadline=None,
                budget_left=None,
                privacy_class="private",
                cancel_token=asyncio.Event(),
            )
            return await adapter.invoke(capability_ref, payload, call_ctx)

        return call_capability

    def _make_emit_event(self, process_id: str, agent_id: str) -> EmitEvent:
        async def emit_event(event_type: str, payload: dict[str, Any]) -> None:
            await self._events.publish(
                event_type,
                source=f"agent.{agent_id}",
                payload=payload,
                process_id=process_id,
            )

        return emit_event

    async def _persist_artifact(self, artifact: Artifact) -> None:
        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
                "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.process_id,
                    artifact.task_id,
                    artifact.type,
                    artifact.path,
                    artifact.mime,
                    artifact.sha256,
                    artifact.bytes,
                    json.dumps(artifact.produced_by, ensure_ascii=False),
                    artifact.supersedes,
                    artifact.created_at,
                ),
            )

        await self._store.write(_insert)
