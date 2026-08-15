"""TaskStore — CRUD cho bảng `tasks` (doc 03 §2.3/§3, đã có từ migration 001,
chưa ai dùng tới trước P-M3-2 — "chừa cột, đừng chừa code", doc 18 §10).

Task = 1 lần gọi Agent hoặc Capability, một BƯỚC trong Workflow YAML (doc 03
§1). Khác `ProcessManager`, TaskStore KHÔNG có bảng transition riêng — vòng
đời Task đơn giản hơn Process nhiều (không WAITING/PAUSED/COMPENSATING, do
chính WorkflowRunner điều khiển tuần tự, không phải API công khai cho caller
ngoài gọi tùy ý) nên chưa cần một máy trạng thái tổng quát như
`kernel/process/manager.py::_VALID_TRANSITIONS` — nếu Task cần trạng thái
phức tạp hơn (parallel cần WAITING chẳng hạn) sẽ thêm đúng lúc cần (P4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiosqlite

from kernel import clock, ids
from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.state.db import StateStore


class TaskState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Task:
    task_id: str
    process_id: str
    step_id: str
    kind: str
    ref: str
    depends_on: list[str]
    state: TaskState
    attempts: int
    quality_score: float | None
    started_at: str | None
    ended_at: str | None
    error_code: str | None


def _row_to_task(row: aiosqlite.Row | tuple[Any, ...]) -> Task:
    return Task(
        task_id=row[0],
        process_id=row[1],
        step_id=row[2],
        kind=row[3],
        ref=row[4],
        depends_on=json.loads(row[5]) if row[5] else [],
        state=TaskState(row[7]),
        attempts=int(row[8]),
        quality_score=row[10],
        started_at=row[11],
        ended_at=row[12],
        error_code=row[13],
    )


_TASK_COLUMNS = (
    "task_id, process_id, step_id, kind, ref, depends_on_json, inputs_json, "
    "state, attempts, idempotency_key, quality_score, started_at, ended_at, error_code"
)


class TaskStore:
    def __init__(self, store: StateStore, events: EventBus) -> None:
        self._store = store
        self._events = events

    async def schedule(
        self,
        *,
        process_id: str,
        step_id: str,
        kind: str,
        ref: str,
        depends_on: list[str],
        inputs: dict[str, Any],
    ) -> Task:
        """Tạo Task ở PENDING + phát `kernel.task.scheduled` (doc 05 §3.1).
        `idempotency_key` = process_id:step_id — 1 step trong 1 Process chỉ có
        đúng 1 Task hàng đầu (attempt tăng qua `retry()`, không tạo Task mới)."""
        task_id = ids.new_id("task")
        idempotency_key = f"{process_id}:{step_id}"

        async def _insert(conn: aiosqlite.Connection) -> Any:
            await conn.execute(
                # _TASK_COLUMNS là hằng số module cố định — S608 báo giả (cùng lý do
                # đã áp dụng cho _PROCESS_COLUMNS ở kernel/process/manager.py).
                f"INSERT INTO tasks({_TASK_COLUMNS}) VALUES "  # noqa: S608
                "(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL, NULL, NULL)",
                (
                    task_id,
                    process_id,
                    step_id,
                    kind,
                    ref,
                    json.dumps(depends_on),
                    json.dumps(inputs, ensure_ascii=False),
                    TaskState.PENDING.value,
                    idempotency_key,
                ),
            )
            return await self._events.build_and_insert(
                conn,
                EventType.TASK_SCHEDULED.value,
                source="kernel.process",
                payload={"task_id": task_id, "step_id": step_id, "attempt": 0},
                process_id=process_id,
                task_id=task_id,
            )

        envelope = await self._store.write(_insert)
        await self._events.dispatch(envelope)
        return await self._require(task_id)

    async def start(self, task_id: str) -> Task:
        task = await self._require(task_id)
        now = clock.now().isoformat()

        async def _update(conn: aiosqlite.Connection) -> Any:
            await conn.execute(
                "UPDATE tasks SET state = ?, started_at = ? WHERE task_id = ?",
                (TaskState.RUNNING.value, now, task_id),
            )
            return await self._events.build_and_insert(
                conn,
                EventType.TASK_STARTED.value,
                source="kernel.process",
                payload={"task_id": task_id, "step_id": task.step_id, "attempt": task.attempts},
                process_id=task.process_id,
                task_id=task_id,
            )

        envelope = await self._store.write(_update)
        await self._events.dispatch(envelope)
        return await self._require(task_id)

    async def succeed(
        self,
        task_id: str,
        *,
        quality_score: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Task:
        """`extra` gộp thêm field TÙY CHỌN vào payload event `kernel.task.completed`
        (KHÔNG ghi vào cột SQL nào — bảng `tasks`, doc 03 §3, là hợp đồng cố định).
        Dùng cho Task loại "parallel" ghi lại thời gian tiết kiệm được so với
        chạy tuần tự (P-M3-4, doc 10 §3 "media (song song, tiết kiệm ...)") mà
        không cần thêm cột hay bảng mới — đúng nguyên tắc "mọi thứ dựng từ
        Event Log" (doc 10 §2)."""
        task = await self._require(task_id)
        now = clock.now().isoformat()

        async def _update(conn: aiosqlite.Connection) -> Any:
            await conn.execute(
                "UPDATE tasks SET state = ?, ended_at = ?, quality_score = ? WHERE task_id = ?",
                (TaskState.SUCCEEDED.value, now, quality_score, task_id),
            )
            payload = {"task_id": task_id, "step_id": task.step_id, "attempt": task.attempts}
            payload.update(extra or {})
            return await self._events.build_and_insert(
                conn,
                EventType.TASK_COMPLETED.value,
                source="kernel.process",
                payload=payload,
                process_id=task.process_id,
                task_id=task_id,
            )

        envelope = await self._store.write(_update)
        await self._events.dispatch(envelope)
        return await self._require(task_id)

    async def fail(self, task_id: str, *, error_code: str) -> Task:
        task = await self._require(task_id)
        now = clock.now().isoformat()

        async def _update(conn: aiosqlite.Connection) -> Any:
            await conn.execute(
                "UPDATE tasks SET state = ?, ended_at = ?, error_code = ? WHERE task_id = ?",
                (TaskState.FAILED.value, now, error_code, task_id),
            )
            return await self._events.build_and_insert(
                conn,
                EventType.TASK_FAILED.value,
                source="kernel.process",
                payload={"task_id": task_id, "step_id": task.step_id, "attempt": task.attempts},
                process_id=task.process_id,
                task_id=task_id,
            )

        envelope = await self._store.write(_update)
        await self._events.dispatch(envelope)
        return await self._require(task_id)

    async def retry(self, task_id: str) -> Task:
        """Tăng `attempts`, đưa lại PENDING — GIỮ NGUYÊN task_id (1 Task = 1
        step_id trong Process, `idempotency_key` không đổi qua các lần thử,
        đúng doc 03 §2.3 `attempts` đếm trên CÙNG một Task, không tạo Task mới
        mỗi lần retry)."""
        task = await self._require(task_id)
        new_attempts = task.attempts + 1

        async def _update(conn: aiosqlite.Connection) -> Any:
            await conn.execute(
                "UPDATE tasks SET state = ?, attempts = ?, started_at = NULL, ended_at = NULL, "
                "error_code = NULL WHERE task_id = ?",
                (TaskState.PENDING.value, new_attempts, task_id),
            )
            return await self._events.build_and_insert(
                conn,
                EventType.TASK_RETRIED.value,
                source="kernel.process",
                payload={"task_id": task_id, "step_id": task.step_id, "attempt": new_attempts},
                process_id=task.process_id,
                task_id=task_id,
            )

        envelope = await self._store.write(_update)
        await self._events.dispatch(envelope)
        return await self._require(task_id)

    async def get(self, task_id: str) -> Task | None:
        async def _get(conn: aiosqlite.Connection) -> Task | None:
            cursor = await conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE task_id = ?",  # noqa: S608
                (task_id,),
            )
            row = await cursor.fetchone()
            return _row_to_task(row) if row is not None else None

        return await self._store.read(_get)

    async def get_by_step(self, process_id: str, step_id: str) -> Task | None:
        """Tra theo `idempotency_key` (process_id:step_id) — WorkflowRunner dùng
        để biết 1 step ĐÃ có Task từ trước (lượt chạy trước, hoặc quay lại do
        `on_fail.goto`) hay chưa, tránh vi phạm UNIQUE(idempotency_key) nếu gọi
        `schedule()` hai lần cho cùng step_id (P-M3-2)."""

        async def _get(conn: aiosqlite.Connection) -> Task | None:
            cursor = await conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE idempotency_key = ?",  # noqa: S608
                (f"{process_id}:{step_id}",),
            )
            row = await cursor.fetchone()
            return _row_to_task(row) if row is not None else None

        return await self._store.read(_get)

    async def list_for_process(self, process_id: str) -> list[Task]:
        async def _list(conn: aiosqlite.Connection) -> list[Task]:
            cursor = await conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks WHERE process_id = ? "  # noqa: S608
                "ORDER BY task_id",
                (process_id,),
            )
            return [_row_to_task(row) for row in await cursor.fetchall()]

        return await self._store.read(_list)

    async def _require(self, task_id: str) -> Task:
        task = await self.get(task_id)
        if task is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Task {task_id} không tồn tại",
                hint="Kiểm lại task_id",
                context={"task_id": task_id},
            )
        return task
