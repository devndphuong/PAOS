"""Bộ chạy Process — KHÔNG phải DAG Scheduler thật (doc 19 P-M1-2/P-M1-3a).

M0 (lát 5c): `on_process_created` chạy TOÀN BỘ agent đồng bộ trong dispatch().
M1-2: tách "đưa vào hàng đợi" (nhanh, đồng bộ) khỏi "thực thi" (nền, qua
`worker_loop()`) — nhưng vẫn 1 job/lần.

M1-3a (lát này): N job chạy THẬT SỰ đồng thời qua `asyncio.Semaphore(max_parallel)`
— dispatcher lấy job khỏi hàng đợi, acquire 1 "slot", `create_task()` rồi lấy
job tiếp theo NGAY, không đợi job trước xong. Job thứ N+1 tự "chờ trong QUEUED"
(đúng backpressure doc 02 §3.2, không cần ngưỡng riêng).

Priority queue: `Process.priority` (0-9, mặc định 5) có từ M0 nhưng chưa ai
dùng — trả nợ "chừa cột" (doc 18 §10). Số LỚN HƠN = ưu tiên CAO HƠN.

Resource token: `ProviderManifest.resources` có từ M0 nhưng chưa ai đọc.
Token gắn với TỪNG LƯỢT GỌI CAPABILITY (không phải cả Process) — đúng khung
doc 02 §3.2 "Task khai báo mình cần token nào". `StubAdapter` khai báo
`resources: []` nên không bị giới hạn thêm ngoài `max_parallel`. KHÔNG hardcode
4 tên resource của doc 02 (gpu/cpu_heavy/net_api/disk_io) — chưa provider nào
cần, đó là trừu tượng hoá sớm (P4).

M1-3b: Cancel (dừng 1 job đang chạy mà không ảnh hưởng job khác) — `cancel()`.

M1-4: Checkpoint & resume. Process = đúng 1 agent (chưa có Task DAG nhiều
bước — đó là M3), nên KHÔNG có tiến độ nội bộ để resume giữa chừng. Checkpoint
chỉ đánh dấu "đã vào RUNNING"; nếu daemon crash giữa lúc RUNNING, khởi động
lại (`apps/paosd/wiring.py::build_daemon()`) đưa process đó lại vào hàng đợi
— `_run_one()` nhận ra nó ĐÃ ở RUNNING (không phải QUEUED) nên không transition
lại, chỉ chạy lại agent từ đầu.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from agents.summarize.agent import SummarizeAgent
from kernel import ids
from kernel.errors import ErrorCode as KernelErrorCode
from kernel.errors import PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.process.manager import Process, ProcessManager, ProcessState
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub.adapter import StubAdapter
from sdk.agent import Agent, AgentContext, AgentError, Artifact, CallCapability, EmitEvent
from sdk.provider import CallContext, ProviderAdapter, ProviderError
from sdk.provider import ErrorCode as SdkErrorCode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PRIORITY = 5
_DEFAULT_MAX_PARALLEL = 3

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

# (-priority, seq, process_id) — PriorityQueue là min-heap nên đảo dấu priority
# (số lớn hơn phải ra trước); seq đơn điệu giữ FIFO khi priority bằng nhau.
_QueueItem = tuple[int, int, str]


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
        *,
        max_parallel: int = _DEFAULT_MAX_PARALLEL,
        resource_capacity: dict[str, int] | None = None,
    ) -> None:
        self._manager = manager
        self._events = events
        self._registry = registry
        self._store = store
        self._workspace_root = workspace_root
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue()
        self._enqueue_seq = itertools.count()
        self._process_slots = asyncio.Semaphore(max_parallel)
        self._resource_semaphores: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(cap) for name, cap in (resource_capacity or {}).items()
        }
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

    async def on_process_created(self, envelope: EventEnvelope) -> None:
        """Chạy đồng bộ trong dispatch() — chỉ 2 lần ghi DB, đủ nhanh để không
        đáng tách. Thực thi Agent thật sự nằm ở worker_loop()/_run_one()."""
        process_id = envelope.process_id
        if process_id is None:
            return  # không nên xảy ra — PROCESS_CREATED luôn có process_id

        # PHẢI idempotent: nếu daemon crash NGAY GIỮA hàm này (giữa lúc PLANNING
        # đã ghi DB nhưng QUEUED thì chưa), EventBus.start() catch-up (REL-01) sẽ
        # gọi lại hàm này lần nữa lúc restart — vì event chưa kịp đánh dấu delivered.
        # Nếu cứ transition() vô điều kiện, lần gọi lại sẽ CONFLICT (không tự
        # transition từ chính nó) và process kẹt ở PLANNING vĩnh viễn (bug thật,
        # phát hiện lúc kiểm thủ công M1-4) — nên phải đọc trạng thái hiện tại
        # trước mỗi bước, chỉ transition nếu còn cần (doc 19 P-M1-4).
        process = await self._manager.get(process_id)
        if process is None:
            return

        # doc 02 §3.1: CREATED -> PLANNING -> QUEUED. PLANNING chưa có logic
        # thật (Workflow YAML engine là M3) — chỉ đi qua để khớp bảng transition
        # đầy đủ (doc 19 P-M1-1).
        if process.state is ProcessState.CREATED:
            process = await self._manager.transition(process_id, ProcessState.PLANNING)
        if process.state is ProcessState.PLANNING:
            process = await self._manager.transition(process_id, ProcessState.QUEUED)
        if process.state is not ProcessState.QUEUED:
            return  # đã bị cancel hoặc đổi trạng thái khác trước khi gọi lại được tới đây

        priority = envelope.payload.get("priority", _DEFAULT_PRIORITY)
        await self._enqueue(process_id, priority)

    async def enqueue_existing(self, process_id: str) -> None:
        """Đưa một process đã ở QUEUED từ trước khi daemon khởi động lại vào
        hàng đợi trong bộ nhớ — bù cho việc `asyncio.PriorityQueue` không sống
        sót qua restart. Gọi từ `apps/paosd/wiring.py::build_daemon()`.
        Process kẹt ở RUNNING lúc crash KHÔNG được đụng tới ở đây — resume
        thật (từ checkpoint) là M1-4, ngoài phạm vi lát này."""
        payload = await self._load_created_payload(process_id)
        priority = payload.get("priority", _DEFAULT_PRIORITY)
        await self._enqueue(process_id, priority)

    async def _enqueue(self, process_id: str, priority: int) -> None:
        seq = next(self._enqueue_seq)
        await self._queue.put((-priority, seq, process_id))

    async def worker_loop(self) -> None:
        """Dispatcher chạy nền vô hạn tới khi bị cancel lúc daemon tắt
        (`Daemon.stop()`). PHẢI chờ có "slot" trống (`max_parallel`) TRƯỚC KHI
        lấy job khỏi hàng đợi — lấy trước rồi mới chờ slot sẽ "khoá" đúng job
        đang xếp hàng đầu tiên vào lúc lấy, phá mất thứ tự ưu tiên nếu job có
        priority cao hơn đến sau khi job đó đã bị lấy ra nhưng chưa chạy được.
        Giao cho một task riêng chạy song song — KHÔNG đợi job đó xong mới lấy
        job tiếp theo."""
        while True:
            await self._process_slots.acquire()
            _, _, process_id = await self._queue.get()
            if process_id in self._running_tasks:
                # Đã có task khác đang chạy CHÍNH process này — có thể xảy ra khi
                # on_process_created() được catch-up gọi lại (REL-01, idempotent
                # theo thiết kế) trùng lúc build_daemon() cũng requeue nó. Bỏ qua,
                # không chạy đúp cùng 1 process (P9 — không tính tiền/side-effect
                # hai lần).
                self._process_slots.release()
                self._queue.task_done()
                continue
            task = asyncio.create_task(self._run_one_and_release(process_id))
            self._running_tasks[process_id] = task
            self._queue.task_done()

    async def _run_one_and_release(self, process_id: str) -> None:
        try:
            await self._run_one(process_id)
        finally:
            self._process_slots.release()
            self._running_tasks.pop(process_id, None)

    async def cancel(self, process_id: str, reason: str = "user_requested") -> Process:
        """Hủy 1 job — không ảnh hưởng job khác đang chạy song song (doc 13 M1
        exit criteria, doc 19 P-M1-3b). Transition DB TRƯỚC (đúng đắn ngay cả
        nếu hủy task thật có vấn đề gì), rồi mới hủy task đang chạy nếu có.
        `ProcessManager.transition()` tự raise CONFLICT nếu process đã ở trạng
        thái cuối — không cần tự kiểm tra lại ở đây. Process còn nằm trong
        hàng đợi (chưa có task) chỉ cần đổi DB — guard ở `_run_one()` đã tự bỏ
        qua khi dispatcher dequeue nó sau đó."""
        updated = await self._manager.transition(process_id, ProcessState.CANCELLED, reason=reason)
        task = self._running_tasks.get(process_id)
        if task is not None:
            task.cancel()
        return updated

    async def _run_one(self, process_id: str) -> None:
        process = await self._manager.get(process_id)
        if process is None or process.state not in (ProcessState.QUEUED, ProcessState.RUNNING):
            return  # đã bị đổi trạng thái trước khi worker kịp chạy (vd cancel — M1-3b)

        workflow_ref = process.workflow_ref
        match = _AGENTS.get(workflow_ref)

        if process.state is ProcessState.QUEUED:
            await self._manager.transition(process_id, ProcessState.RUNNING)
            await self._manager.write_checkpoint(process_id, {"phase": "running"})
        # Nếu ĐÃ ở RUNNING (resume sau crash — doc 19 P-M1-4), không transition lại:
        # đây là CÙNG một lượt RUNNING từ trước khi daemon chết, chỉ chạy lại agent
        # từ đầu vì Process = 1 agent, không có tiến độ nội bộ để resume giữa chừng.

        if match is None:
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.NOT_FOUND.value,
                error_message=f"Không có agent nào khớp workflow_ref '{workflow_ref}'",
            )
            return

        agent, prompts_dir = match
        payload = await self._load_created_payload(process_id)
        spec = payload.get("spec", {})
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

    async def _load_created_payload(self, process_id: str) -> dict[str, Any]:
        """`spec`/`priority` job gốc không nằm trên `Process` — đọc lại từ
        payload event `kernel.process.created` (durable) thay vì thêm cách đọc
        bảng `jobs` riêng chỉ để phục vụ vài chỗ dùng (doc 19 P-M1-2/3a)."""
        trace = await self._events.events_for_process(process_id)
        for envelope in trace:
            if envelope.type == EventType.PROCESS_CREATED.value:
                return envelope.payload
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

    @asynccontextmanager
    async def _hold_resources(self, resource_names: list[str]) -> AsyncIterator[None]:
        """Giữ semaphore của TỪNG resource được khai báo (bỏ qua tên không cấu
        hình dung lượng — coi như không giới hạn). Đây là nơi thật sự áp
        "resource token" (doc 02 §3.2), gắn với lượt gọi capability chứ không
        phải cả Process."""
        async with AsyncExitStack() as stack:
            for name in resource_names:
                sem = self._resource_semaphores.get(name)
                if sem is not None:
                    await stack.enter_async_context(sem)
            yield

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
            async with self._hold_resources(adapter.manifest.resources):
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
