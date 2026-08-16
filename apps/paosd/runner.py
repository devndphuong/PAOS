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

M2-1 (doc 19): trước lát này, adapter provider được chọn qua `_ADAPTERS` —
dict tĩnh hardcode trong file này, vi phạm đúng exit criteria doc 13 M2
"provider mới thêm vào chỉ bằng 1 file adapter + 1 YAML" (thêm provider mới
còn phải SỬA CODE ở đây). Giờ dùng `Registry.load_adapter()` (nạp động qua
`importlib`, đọc `provider.yaml::adapter`) — không còn dict hardcode nào.

M2-3 (doc 19): `_make_call_capability()` không tự chọn provider nữa — giao
hết cho `apps/paosd/router.py::Router` (ràng buộc cứng, fallback thật theo
kết quả `invoke()`, circuit breaker, Decision Record). Resource token
(`_hold_resources`, đoạn ghi chú ở trên) cũng chuyển sang Router cùng lúc —
mỗi candidate trong chuỗi fallback có thể khai `resources` khác nhau.

P-M3-4 (trả nợ BL-006): trước lát này, agent được chọn qua `_AGENTS` — dict
tĩnh hardcode trong file này, import trực tiếp từng agent, vi phạm đúng exit
criteria doc 13 M3 "thêm agent mới không sửa Runner". Giờ dùng
`Registry.load_agent()` (nạp động qua `importlib`, đọc `manifest.yaml::entry`)
— không còn dict hardcode, không còn `from agents.xxx.agent import ...` tĩnh
nào ở file này (cùng hình dạng `load_adapter()` cho provider, M2-1).
"""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

import aiosqlite

from apps.paosd.router import Router
from apps.paosd.workflow_runner import StepExecutionFailed, WorkflowRunner
from kernel.errors import ErrorCode as KernelErrorCode
from kernel.errors import PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.process.manager import Process, ProcessManager, ProcessState
from kernel.redact import redact, redact_deep
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.agent import (
    Agent,
    AgentContext,
    AgentError,
    Artifact,
    CallCapability,
    EmitEvent,
    ExecResult,
    ReportProgress,
    WriteCheckpoint,
)
from sdk.provider import ErrorCode as SdkErrorCode
from sdk.provider import ProviderError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PRIORITY = 5
_DEFAULT_MAX_PARALLEL = 3

# (-priority, seq, process_id) — PriorityQueue là min-heap nên đảo dấu priority
# (số lớn hơn phải ra trước); seq đơn điệu giữ FIFO khi priority bằng nhau.
_QueueItem = tuple[int, int, str]


def _resolve_agent(registry: Registry, workflow_ref: str) -> tuple[Agent, Path] | None:
    """`workflow_ref` dạng "agent:<id>@<version>" -> (instance, prompts_dir)
    qua `Registry.load_agent()` (P-M3-4, trả nợ BL-006) — thay hoàn toàn dict
    tĩnh `_AGENTS` cũ. Trả None nếu không đăng ký, để caller tự quyết định mã
    lỗi/hint (không phải trách nhiệm của hàm tra cứu thuần này)."""
    if not workflow_ref.startswith("agent:"):
        return None
    ref = workflow_ref.removeprefix("agent:")
    agent_id, _, version_str = ref.partition("@")
    if not version_str.isdigit():
        return None
    version = int(version_str)
    try:
        agent = registry.load_agent(agent_id, version)
        prompts_dir = registry.agent_extra_dir(agent_id, version) / "prompts"
    except PaosError:
        return None
    return agent, prompts_dir


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
        energy_policy_path: Path | None = None,
        time_policy_path: Path | None = None,
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
        # `energy_policy_path`/`time_policy_path` — None -> để Router tự dùng
        # default trỏ policies/{energy,time}.yaml THẬT (production). Test truyền
        # đường dẫn không tồn tại để tránh flaky theo tải CPU/NGÀY GIỜ MÁY THẬT
        # (cùng lý do apps/paosd/router.py::Router — energy P-M7-2, time P-M7-3).
        router_kwargs: dict[str, Path] = {}
        if energy_policy_path is not None:
            router_kwargs["energy_policy_path"] = energy_policy_path
        if time_policy_path is not None:
            router_kwargs["time_policy_path"] = time_policy_path
        self._router = Router(
            registry, events, store, self._resource_semaphores, workspace_root, **router_kwargs
        )
        self._workflow_runner = WorkflowRunner(
            manager, events, registry, store, self._router, workspace_root
        )

    @property
    def router(self) -> Router:
        """Lộ ra cho `apps/paosd/app.py` dựng `MemoryRetriever` (P-M5-1) — tái
        dùng CÙNG instance Router (breaker state/cache nhất quán), không dựng
        Router thứ 2 song song."""
        return self._router

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
        qua khi dispatcher dequeue nó sau đó.

        `error_code=CANCELLED` được gán ở đây (phát hiện lúc soát error taxonomy
        M1-6) — trước đó process bị hủy có `error_code=NULL`, không nhất quán
        với FAILED (luôn có error_code). CANCELLED trong 13 mã chuẩn (doc 04 §1)
        chưa từng được dùng ở đâu tới lát này."""
        updated = await self._manager.transition(
            process_id,
            ProcessState.CANCELLED,
            reason=reason,
            error_code=KernelErrorCode.CANCELLED.value,
        )
        task = self._running_tasks.get(process_id)
        if task is not None:
            task.cancel()
        return updated

    async def _run_one(self, process_id: str) -> None:
        process = await self._manager.get(process_id)
        if process is None or process.state not in (ProcessState.QUEUED, ProcessState.RUNNING):
            return  # đã bị đổi trạng thái trước khi worker kịp chạy (vd cancel — M1-3b)

        workflow_ref = process.workflow_ref
        # Đã ở RUNNING TRƯỚC khi vào đây == vừa được đưa lại hàng đợi sau crash
        # (`enqueue_existing()`, doc 19 P-M1-4), KHÔNG phải job mới. Đây là điều
        # kiện DUY NHẤT để cân nhắc resume() thay vì chạy lại từ đầu (P-M3-1).
        was_resuming = process.state is ProcessState.RUNNING

        if process.state is ProcessState.QUEUED:
            await self._manager.transition(process_id, ProcessState.RUNNING)
            await self._manager.write_checkpoint(process_id, {"phase": "running"})
        # Nếu ĐÃ ở RUNNING (resume sau crash — doc 19 P-M1-4), không transition lại:
        # đây là CÙNG một lượt RUNNING từ trước khi daemon chết.

        # P-M3-2: workflow_ref "workflow:<id>@<version>" đi đường DAG nhiều step
        # (WorkflowRunner), khác hẳn "agent:<id>@<version>" (1 agent = cả Process,
        # M0-M2). WorkflowRunner tự quyết định SUCCEEDED/FAILED/COMPENSATING —
        # return ngay, không rơi xuống nhánh agent đơn bên dưới.
        if workflow_ref.startswith("workflow:"):
            await self._run_workflow(process_id, workflow_ref)
            return

        match = _resolve_agent(self._registry, workflow_ref)
        if match is None:
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.NOT_FOUND.value,
                error_message=redact(f"Không có agent nào khớp workflow_ref '{workflow_ref}'"),
            )
            return

        agent, prompts_dir = match
        payload = await self._load_created_payload(process_id)
        spec = payload.get("spec", {})
        try:
            # checkpoint_seq=1 là mốc "phase: running" ghi ở trên (không phải
            # trạng thái nội bộ thật của agent) — chỉ resume() thật khi agent tự
            # ghi checkpoint (seq > 1) qua ctx.checkpoint() VÀ khai báo
            # checkpointable: true. Không đủ cả hai -> chạy lại từ đầu như M1-4,
            # đúng "an toàn hơn là sai" (agent không checkpointable không có gì
            # để resume từ đó).
            checkpoint = (
                await self._manager.get_latest_checkpoint(process_id)
                if was_resuming and agent.manifest.checkpointable
                else None
            )
            if checkpoint is not None and checkpoint["seq"] > 1:
                await self._resume_agent(agent, prompts_dir, process_id, checkpoint["state"])
            else:
                await self._run_agent(agent, prompts_dir, process_id, spec)
        except (AgentError, ProviderError, PaosError) as exc:
            # redact() ở đây (doc 09 §6, SEC-01) — error_message có thể echo lại nội
            # dung từ provider/agent thật (vd lỗi HTTP kèm header), chưa chắc đã qua
            # 1 lớp lọc nào trước khi tới đây.
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=exc.code.value,
                error_message=redact(exc.message),
            )
            return
        except Exception as exc:  # an toàn cuối — process không bao giờ được kẹt ở RUNNING
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.INTERNAL.value,
                error_message=redact(str(exc)),
            )
            return

        await self._manager.transition(process_id, ProcessState.SUCCEEDED)

    async def _run_workflow(self, process_id: str, workflow_ref: str) -> None:
        """P-M3-2 — đối xứng với `_run_agent`/`_resume_agent` nhưng cho
        `workflow:<id>@<version>`. Tự quản lý toàn bộ transition cuối
        (SUCCEEDED / FAILED / FAILED->COMPENSATING->FAILED_FINAL) vì chỉ ở
        đây mới đọc được `spec.on_error` để biết có compensate hay không."""
        ref = workflow_ref.removeprefix("workflow:")
        workflow_id, _, version_str = ref.partition("@")
        try:
            spec = self._registry.get_workflow(workflow_id, int(version_str or "0"))
        except (PaosError, ValueError) as exc:
            code = (
                exc.code.value
                if isinstance(exc, PaosError)
                else KernelErrorCode.INVALID_INPUT.value
            )
            message = exc.message if isinstance(exc, PaosError) else str(exc)
            await self._manager.transition(
                process_id, ProcessState.FAILED, error_code=code, error_message=redact(message)
            )
            return

        payload = await self._load_created_payload(process_id)
        inputs = payload.get("spec", {})
        try:
            await self._workflow_runner.run(process_id, spec, inputs)
        except StepExecutionFailed as failure:
            cause = failure.cause
            code = cause.code.value if hasattr(cause, "code") else KernelErrorCode.INTERNAL.value
            message = getattr(cause, "message", str(cause))
            await self._manager.transition(
                process_id, ProcessState.FAILED, error_code=code, error_message=redact(message)
            )
            if spec.on_error.get("compensate"):
                await self._manager.transition(process_id, ProcessState.COMPENSATING)
                await self._workflow_runner.compensate(process_id, spec, failure.context)
                await self._manager.transition(process_id, ProcessState.FAILED_FINAL)
            return
        except Exception as exc:  # an toàn cuối — process không bao giờ được kẹt ở RUNNING
            await self._manager.transition(
                process_id,
                ProcessState.FAILED,
                error_code=KernelErrorCode.INTERNAL.value,
                error_message=redact(str(exc)),
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

    def _build_agent_context(
        self, agent: Agent, prompts_dir: Path, process_id: str
    ) -> AgentContext:
        return AgentContext(
            process_id=process_id,
            task_id=None,
            workspace_dir=self._workspace_root,
            agent_id=agent.manifest.agent_id,
            prompts_dir=prompts_dir,
            manifest=agent.manifest,
            persist_artifact=self._persist_artifact,
            call_capability=self._make_call_capability(process_id),
            emit_event=self._make_emit_event(process_id, agent.manifest.agent_id),
            report_progress=self._make_report_progress(process_id),
            write_checkpoint=self._make_write_checkpoint(process_id),
        )

    async def _run_agent(
        self, agent: Agent, prompts_dir: Path, process_id: str, spec: dict[str, Any]
    ) -> None:
        ctx = self._build_agent_context(agent, prompts_dir, process_id)
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
        await self._finish_agent(agent, exec_result)

    async def _resume_agent(
        self, agent: Agent, prompts_dir: Path, process_id: str, checkpoint_state: dict[str, Any]
    ) -> None:
        """Chỉ gọi khi `agent.manifest.checkpointable` và có checkpoint seq > 1
        (doc 04 §3.1, P-M3-1) — bỏ qua validate()/think()/execute(), agent tự
        biết cách dựng lại ExecResult từ trạng thái đã checkpoint."""
        ctx = self._build_agent_context(agent, prompts_dir, process_id)
        await agent.initialize(ctx)
        exec_result = await agent.resume(checkpoint_state)
        await self._finish_agent(agent, exec_result)

    async def _finish_agent(self, agent: Agent, exec_result: ExecResult) -> None:
        review = await agent.review(exec_result)
        if not review.passed:
            raise AgentError(
                SdkErrorCode.QUALITY_BELOW_THRESHOLD,
                f"review() từ chối kết quả: {review.reason}",
                hint="Xem lại prompt hoặc input — kết quả không đạt ngưỡng tối thiểu",
            )

        await agent.publish(exec_result)

    def _make_call_capability(self, process_id: str) -> CallCapability:
        async def call_capability(
            capability_ref: str,
            payload: dict[str, Any],
            exclude_provider: str | None,
            contains_private_l3: bool,
        ) -> tuple[dict[str, Any], str | None]:
            return await self._router.call(
                capability_ref,
                payload,
                process_id,
                exclude_provider=exclude_provider,
                contains_private_l3=contains_private_l3,
            )

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

    def _make_report_progress(self, process_id: str) -> ReportProgress:
        async def report_progress(pct: float, message: str) -> None:
            await self._manager.report_progress(process_id, pct, message)

        return report_progress

    def _make_write_checkpoint(self, process_id: str) -> WriteCheckpoint:
        async def write_checkpoint(state: dict[str, Any]) -> int:
            return await self._manager.write_checkpoint(process_id, state)

        return write_checkpoint

    async def _persist_artifact(self, artifact: Artifact) -> None:
        # redact() cho path (tên/đường dẫn artifact — 1 vị trí doc 19 P-M2-5 nêu tên)
        # + redact_deep() cho produced_by (dict tự do agent điền, có thể chứa prompt/
        # tham số gọi provider) — cả 2 trước khi ghi (doc 09 §6, SEC-01).
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
                    redact(artifact.path),
                    artifact.mime,
                    artifact.sha256,
                    artifact.bytes,
                    json.dumps(redact_deep(artifact.produced_by), ensure_ascii=False),
                    artifact.supersedes,
                    artifact.created_at,
                ),
            )

        await self._store.write(_insert)
