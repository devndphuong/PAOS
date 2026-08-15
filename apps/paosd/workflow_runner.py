"""WorkflowRunner — thực thi DAG đã parse bởi kernel/workflow/spec.py (doc 04
§4, doc 19 P-M3-2). Ở apps/, KHÔNG ở kernel/: mỗi step gọi Capability (qua
Router) hoặc chạy trọn vòng đời 6 bước của Agent (qua AgentContext) — cả hai
đều thuộc sdk/agents/, Kernel bị cấm biết tới (MNT-06).

Trùng lặp có chủ đích với `apps/paosd/runner.py::Runner._run_agent` (build
AgentContext, gọi initialize/validate/think/execute/review/publish): Runner
chạy 1 agent = TOÀN BỘ Process (M0-M2), còn ở đây 1 agent = MỘT STEP trong
DAG lớn hơn (task_id thật, context đến từ output step khác, không phải spec
job gốc). Hai hình dạng đủ khác nhau để chưa đáng trừu tượng hoá chung (P4) —
gộp lại khi có bằng chứng thật (vd khi thêm resume() cho từng step)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from apps.paosd.router import Router
from kernel import clock
from kernel.errors import ErrorCode as KernelErrorCode
from kernel.errors import PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.process.manager import ProcessManager
from kernel.process.tasks import Task, TaskStore
from kernel.redact import redact, redact_deep
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from kernel.workflow import expr
from kernel.workflow.spec import Step, WorkflowSpec, to_json_dict, topological_order
from sdk.agent import (
    AgentContext,
    AgentError,
    Artifact,
    CallCapability,
    EmitEvent,
    ReportProgress,
    WriteCheckpoint,
)
from sdk.provider import ErrorCode as SdkErrorCode
from sdk.provider import ProviderError


class StepExecutionFailed(Exception):
    """1 step thất bại sau khi hết retry/loop — bọc lỗi gốc (AgentError/
    ProviderError/PaosError, đều có `.code`/`.message`) để `Runner._run_workflow`
    biết error_code chuẩn nào ghi vào Process. `context` được `run()` gắn vào
    TRƯỚC KHI ném lại — chứa output mọi step đã thành công tính tới lúc lỗi,
    để `compensate()` có thể tham chiếu qua `${steps...}` nếu cần."""

    def __init__(self, step_id: str, cause: Exception) -> None:
        super().__init__(f"Step '{step_id}' thất bại: {cause}")
        self.step_id = step_id
        self.cause = cause
        self.context: dict[str, Any] = {}


def _duration_ms_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() * 1000)


def _iso_duration_ms(start_iso: str, end_iso: str) -> int:
    return _duration_ms_between(datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso))


class WorkflowRunner:
    def __init__(
        self,
        manager: ProcessManager,
        events: EventBus,
        registry: Registry,
        store: StateStore,
        router: Router,
        workspace_root: Path,
    ) -> None:
        """P-M3-4 (trả nợ BL-006): agent cho step `kind: agent` nạp qua
        `registry.load_agent()`/`agent_extra_dir()` — không còn dict `agents`
        truyền tay như P-M3-2/3 (dict đó tồn tại chỉ để né import vòng, giờ
        không cần nữa vì Registry đã biết cách nạp Agent, giống Provider)."""
        self._manager = manager
        self._events = events
        self._registry = registry
        self._store = store
        self._router = router
        self._workspace_root = workspace_root
        self._tasks = TaskStore(store, events)

    async def run(
        self, process_id: str, spec: WorkflowSpec, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        """Chạy trọn DAG cho 1 Process, trả về context cuối (steps đã chạy) khi
        THÀNH CÔNG. Raise `StepExecutionFailed` (có `.context` gắn sẵn — xem
        docstring class) nếu 1 step thất bại không thể phục hồi (hết retry,
        hết loop budget) — caller (`Runner._run_workflow`) quyết định FAILED
        hay FAILED->COMPENSATING->FAILED_FINAL dựa trên `spec.on_error`."""
        await self._freeze_workflow_json(process_id, spec)
        context: dict[str, Any] = {"inputs": inputs, "steps": {}}
        try:
            await self._execute_chain(process_id, spec, context)
        except StepExecutionFailed as failure:
            failure.context = context
            raise
        return context

    async def compensate(
        self, process_id: str, spec: WorkflowSpec, context: dict[str, Any]
    ) -> None:
        """Best-effort: chạy từng step_id liệt kê trong `on_error.compensate`
        (doc 04 §4). Step nào lỗi tiếp trong lúc compensate cũng KHÔNG chặn
        các step compensate còn lại — dọn dẹp cố hết sức, không dừng giữa
        chừng vì 1 bước dọn thất bại."""
        names: list[str] = list(spec.on_error.get("compensate", []))
        if not names:
            return
        await self._events.publish(
            EventType.WORKFLOW_COMPENSATION_STARTED.value,
            source="kernel.workflow",
            payload={"steps": names},
            process_id=process_id,
        )
        steps_by_id = {s.step_id: s for s in spec.steps}
        for name in names:
            step = steps_by_id.get(name)
            if step is None:
                continue  # không khớp step nào đã khai báo — bỏ qua, xem docstring module
            try:
                await self._run_single_step_with_retry(process_id, step, context)
            except StepExecutionFailed:
                continue  # dọn tiếp các bước còn lại, không để 1 lỗi chặn hết

    async def _execute_chain(
        self, process_id: str, spec: WorkflowSpec, context: dict[str, Any]
    ) -> None:
        order = topological_order(spec)
        steps_by_id = {s.step_id: s for s in spec.steps}
        index_of = {step_id: i for i, step_id in enumerate(order)}
        loop_counts: dict[tuple[str, str], int] = {}

        i = 0
        while i < len(order):
            step = steps_by_id[order[i]]
            try:
                await self._run_step(process_id, step, context)
            except StepExecutionFailed:
                if step.on_fail is not None:
                    key = (step.on_fail.goto, step.step_id)
                    count = loop_counts.get(key, 0)
                    if count < step.on_fail.max_loops:
                        loop_counts[key] = count + 1
                        await self._events.publish(
                            EventType.WORKFLOW_LOOP_ENTERED.value,
                            source="kernel.workflow",
                            payload={"step_id": step.on_fail.goto, "loop_count": count + 1},
                            process_id=process_id,
                        )
                        context["carry"] = expr.resolve_deep(step.on_fail.carry, context)
                        i = index_of[step.on_fail.goto]
                        continue
                raise
            i += 1

    async def _run_step(self, process_id: str, step: Step, context: dict[str, Any]) -> None:
        if step.when is not None and not expr.resolve(step.when, context):
            await self._events.publish(
                EventType.WORKFLOW_STEP_SKIPPED.value,
                source="kernel.workflow",
                payload={"step_id": step.step_id, "condition": step.when},
                process_id=process_id,
            )
            context["steps"][step.step_id] = {"output": None, "skipped": True}
            return
        if step.kind == "parallel":
            await self._run_parallel(process_id, step, context)
            return
        await self._run_single_step_with_retry(process_id, step, context)

    async def _run_parallel(self, process_id: str, group: Step, context: dict[str, Any]) -> None:
        """`group` cũng là 1 Task (kind="parallel") — không chỉ các sub-step —
        để có chỗ ghi lại "tiết kiệm bao nhiêu so với tuần tự" (doc 10 §3, P-M3-4)
        vào Event Log, không bịa số: `sequential_ms` = TỔNG thời gian đo được
        thật của từng sub-step (started_at/ended_at trong `tasks`, không phải
        hằng số giả định); `wall_ms` = thời gian thật đã trôi qua của cả nhóm."""
        group_task, _ = await self._schedule_or_retry_task(
            process_id, group.step_id, "parallel", "", {}
        )
        await self._tasks.start(group_task.task_id)
        wall_start = clock.now()
        results = await asyncio.gather(
            *(
                self._run_single_step_with_retry(process_id, sub, context)
                for sub in group.sub_steps
            ),
            return_exceptions=True,
        )
        wall_ms = _duration_ms_between(wall_start, clock.now())
        # doc 13 M3 exit criteria LOC-05 (P-M3-5): sub-step required=False thất
        # bại KHÔNG làm cả group thất bại — Task của chính nó đã ghi FAILED
        # thật ở _run_single_step_with_retry() (không lỗi âm thầm), ta chỉ
        # KHÔNG lan lỗi lên trên, coi như "bỏ qua có kiểm soát" và phát thêm
        # workflow.step.skipped để rõ ràng đây là quyết định, không phải bug.
        for sub, result in zip(group.sub_steps, results, strict=True):
            if not isinstance(result, BaseException):
                continue
            if sub.required:
                await self._tasks.fail(
                    group_task.task_id, error_code=KernelErrorCode.DEPENDENCY_FAILED.value
                )
                raise result
            context["steps"][sub.step_id] = {"output": None, "skipped": True}
            await self._events.publish(
                EventType.WORKFLOW_STEP_SKIPPED.value,
                source="kernel.workflow",
                payload={
                    "step_id": sub.step_id,
                    "condition": f"required: false, degraded sau khi thất bại: {result}",
                },
                process_id=process_id,
            )
        context["steps"][group.step_id] = {
            sub.step_id: context["steps"][sub.step_id] for sub in group.sub_steps
        }

        sequential_ms = 0
        for sub in group.sub_steps:
            sub_task = await self._tasks.get_by_step(process_id, sub.step_id)
            if sub_task is not None and sub_task.started_at and sub_task.ended_at:
                sequential_ms += _iso_duration_ms(sub_task.started_at, sub_task.ended_at)
        saved_ms = max(0, sequential_ms - wall_ms)
        await self._tasks.succeed(
            group_task.task_id,
            extra={
                "parallel": {
                    "wall_ms": wall_ms,
                    "sequential_ms": sequential_ms,
                    "saved_ms": saved_ms,
                }
            },
        )

    async def _schedule_or_retry_task(
        self, process_id: str, step_id: str, kind: str, ref: str, inputs: dict[str, Any]
    ) -> tuple[Task, int]:
        """`idempotency_key` (process_id:step_id) là UNIQUE (doc 03 §3) — step
        này có thể đã có Task từ lượt chạy TRƯỚC trong CÙNG Process (on_fail.goto
        quay lại một step đã qua, hoặc compensate() chạy 1 step đã chạy rồi).
        Phải retry() Task CŨ, không schedule() Task mới, nếu không vi phạm
        UNIQUE. Dùng chung cho step đơn (`_run_single_step_with_retry`) VÀ
        group `parallel` (`_run_parallel`) — cả hai đều là 1 Task trong DAG."""
        existing = await self._tasks.get_by_step(process_id, step_id)
        if existing is None:
            task = await self._tasks.schedule(
                process_id=process_id,
                step_id=step_id,
                kind=kind,
                ref=ref,
                depends_on=[],
                inputs=inputs,
            )
            return task, 0
        task = await self._tasks.retry(existing.task_id)
        return task, task.attempts

    async def _run_single_step_with_retry(
        self, process_id: str, step: Step, context: dict[str, Any]
    ) -> None:
        resolved_inputs = expr.resolve_deep(step.with_, context)
        task, attempt = await self._schedule_or_retry_task(
            process_id, step.step_id, step.kind, step.ref or "", resolved_inputs
        )
        while True:
            await self._tasks.start(task.task_id)
            try:
                output = await self._invoke_step(process_id, step, task, resolved_inputs)
            except (AgentError, ProviderError, PaosError) as exc:
                await self._tasks.fail(task.task_id, error_code=exc.code.value)
                if attempt < step.retry.max:
                    attempt += 1
                    task = await self._tasks.retry(task.task_id)
                    continue
                raise StepExecutionFailed(step.step_id, exc) from exc
            else:
                await self._tasks.succeed(task.task_id)
                context["steps"][step.step_id] = {"output": output, "skipped": False}
                return

    async def _invoke_step(
        self, process_id: str, step: Step, task: Task, resolved_inputs: dict[str, Any]
    ) -> Any:
        if step.kind == "capability":
            if step.ref is None:  # validate_workflow() đảm bảo không xảy ra — an toàn cuối
                raise RuntimeError(f"Step capability '{step.step_id}' thiếu 'ref' — lỗi lập trình")
            return await self._router.call(step.ref, resolved_inputs, process_id)
        if step.kind != "agent":
            # "parallel" đã xử lý ở _run_step() trước khi tới đây — không nên tới nhánh này
            raise RuntimeError(
                f"Step kind '{step.kind}' không hợp lệ ở _invoke_step — lỗi lập trình"
            )
        return await self._run_agent_step(process_id, step, task, resolved_inputs)

    async def _run_agent_step(
        self, process_id: str, step: Step, task: Task, resolved_inputs: dict[str, Any]
    ) -> dict[str, Any]:
        agent_id, _, version_str = (step.ref or "").partition("@")
        if not version_str.isdigit():
            raise PaosError(
                KernelErrorCode.INVALID_INPUT,
                f"Step '{step.step_id}': ref '{step.ref}' không đúng dạng agent_id@version",
                hint="ref của step kind=agent phải dạng '<agent_id>@<version>', vd script.agent@1",
                context={"step_id": step.step_id, "ref": step.ref},
            )
        version = int(version_str)
        try:
            agent = self._registry.load_agent(agent_id, version)
            prompts_dir = self._registry.agent_extra_dir(agent_id, version) / "prompts"
        except PaosError as exc:
            raise PaosError(
                KernelErrorCode.NOT_FOUND,
                f"Không có agent nào khớp ref '{step.ref}' cho step "
                f"'{step.step_id}': {exc.message}",
                hint="Kiểm 'ref' trong workflow.yaml khớp đúng agent_id@version đã đăng ký",
                context={"step_id": step.step_id, "ref": step.ref},
            ) from exc
        ctx = AgentContext(
            process_id=process_id,
            task_id=task.task_id,
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
        await agent.initialize(ctx)

        validation = await agent.validate(resolved_inputs)
        if not validation.ok:
            raise AgentError(
                SdkErrorCode.INVALID_INPUT,
                f"validate() từ chối input ở step '{step.step_id}': {validation.reason}",
                hint="Kiểm lại 'with' của step trong workflow.yaml — xem manifest.needs của agent",
            )

        plan = await agent.think(resolved_inputs)
        exec_result = await agent.execute(plan)

        review = await agent.review(exec_result)
        if not review.passed:
            raise AgentError(
                SdkErrorCode.QUALITY_BELOW_THRESHOLD,
                f"review() từ chối kết quả ở step '{step.step_id}': {review.reason}",
                hint="Xem lại prompt hoặc input — kết quả không đạt ngưỡng tối thiểu",
            )

        artifacts = await agent.publish(exec_result)
        return {**exec_result.data, "artifacts": [a.artifact_id for a in artifacts]}

    async def _freeze_workflow_json(self, process_id: str, spec: WorkflowSpec) -> None:
        """doc 03 §4 — workflow.json đã resolve đóng băng vào project để tái lập
        được sau này. `projects/<process_id>/` là quy ước TẠM (process_id thay
        cho slug người đọc được) — Project thật (project.json, job.json, slug)
        vẫn là nợ BL-003, hoãn tới M6 (doc backlog.md)."""
        project_dir = self._workspace_root / "projects" / process_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "workflow.json").write_text(
            json.dumps(to_json_dict(spec), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _make_call_capability(self, process_id: str) -> CallCapability:
        async def call_capability(capability_ref: str, payload: dict[str, Any]) -> dict[str, Any]:
            return await self._router.call(capability_ref, payload, process_id)

        return call_capability

    def _make_emit_event(self, process_id: str, agent_id: str) -> EmitEvent:
        async def emit_event(event_type: str, payload: dict[str, Any]) -> None:
            await self._events.publish(
                event_type, source=f"agent.{agent_id}", payload=payload, process_id=process_id
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
