"""ProcessManager — máy trạng thái Process, bảng chuyển trạng thái là DỮ LIỆU (doc 02 §3.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

import aiosqlite

from kernel import clock, ids
from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.state.db import StateStore


class ProcessState(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


# Bảng đầy đủ theo doc 02 §3.1 (doc 19 P-M1-1). COMPENSATING/FAILED_FINAL có
# đường vào HỢP LỆ trong bảng nhưng chưa ai tự động kích hoạt ở M1 — compensation
# thật (workflow YAML `compensate: [...]`) là M3 (doc 13), không phải M1. Đây là
# "chừa cột, đừng chừa code" áp cho transition: mở đường, chưa viết logic driver.
_VALID_TRANSITIONS: dict[ProcessState, frozenset[ProcessState]] = {
    ProcessState.CREATED: frozenset({ProcessState.PLANNING, ProcessState.CANCELLED}),
    ProcessState.PLANNING: frozenset({ProcessState.QUEUED, ProcessState.CANCELLED}),
    ProcessState.QUEUED: frozenset({ProcessState.RUNNING, ProcessState.CANCELLED}),
    ProcessState.RUNNING: frozenset(
        {
            ProcessState.SUCCEEDED,
            ProcessState.FAILED,
            ProcessState.WAITING,
            ProcessState.PAUSED,
            ProcessState.CANCELLED,
        }
    ),
    ProcessState.WAITING: frozenset({ProcessState.RUNNING, ProcessState.CANCELLED}),
    ProcessState.PAUSED: frozenset({ProcessState.RUNNING, ProcessState.CANCELLED}),
    ProcessState.SUCCEEDED: frozenset(),
    ProcessState.FAILED: frozenset({ProcessState.COMPENSATING, ProcessState.FAILED_FINAL}),
    ProcessState.COMPENSATING: frozenset({ProcessState.FAILED_FINAL}),
    ProcessState.FAILED_FINAL: frozenset(),
    ProcessState.CANCELLED: frozenset(),
}

_PROCESS_COLUMNS = (
    "process_id, pid, job_id, name, workflow_ref, state, progress, "
    "checkpoint_seq, started_at, ended_at, error_code, error_json"
)


def _select_processes_sql(where: str = "") -> str:
    """Xây câu SELECT processes. `_PROCESS_COLUMNS` là hằng số module cố định,
    không phải input người dùng — mọi giá trị lọc thật (pid, state...) vẫn qua
    tham số hóa `?` ở nơi gọi, không nội suy trực tiếp. S608 báo giả ở đây."""
    return f"SELECT {_PROCESS_COLUMNS} FROM processes{where}"  # noqa: S608


@dataclass(frozen=True)
class Process:
    process_id: str
    pid: int
    job_id: str
    name: str
    workflow_ref: str
    state: ProcessState
    progress: float
    checkpoint_seq: int
    started_at: str | None
    ended_at: str | None
    error_code: str | None
    error_json: str | None


def _row_to_process(row: aiosqlite.Row | tuple[Any, ...]) -> Process:
    return Process(
        process_id=row[0],
        pid=row[1],
        job_id=row[2],
        name=row[3],
        workflow_ref=row[4],
        state=ProcessState(row[5]),
        progress=row[6],
        checkpoint_seq=row[7],
        started_at=row[8],
        ended_at=row[9],
        error_code=row[10],
        error_json=row[11],
    )


def _duration_ms(started_at: str | None, ended_at: str) -> int:
    if started_at is None:
        return 0
    start = datetime.fromisoformat(started_at)
    end = datetime.fromisoformat(ended_at)
    return int((end - start).total_seconds() * 1000)


# Target state -> event có payload chỉ {pid}. Gom vào dict để _event_for_transition()
# không vượt giới hạn số lần return (PLR0911) — các trạng thái cần payload khác
# (RUNNING, WAITING/PAUSED, SUCCEEDED, FAILED, CANCELLED) vẫn xử lý riêng bên dưới.
_PID_ONLY_EVENT_FOR_STATE: dict[ProcessState, EventType] = {
    ProcessState.PLANNING: EventType.PROCESS_PLANNING,
    ProcessState.QUEUED: EventType.PROCESS_QUEUED,
    ProcessState.COMPENSATING: EventType.PROCESS_COMPENSATING,
    ProcessState.FAILED_FINAL: EventType.PROCESS_FAILED_FINAL,
}

# WAITING/PAUSED đều mang payload {pid, reason} (doc 05).
_REASON_EVENT_FOR_STATE: dict[ProcessState, EventType] = {
    ProcessState.WAITING: EventType.PROCESS_WAITING,
    ProcessState.PAUSED: EventType.PROCESS_PAUSED,
}


def _event_for_transition(
    from_state: ProcessState,
    to_state: ProcessState,
    pid: int,
    *,
    started_at: str | None,
    ended_at: str | None,
    error_code: str | None,
    reason: str | None,
) -> tuple[str, dict[str, Any]]:
    pid_only = _PID_ONLY_EVENT_FOR_STATE.get(to_state)
    if pid_only is not None:
        return pid_only.value, {"pid": pid}
    with_reason = _REASON_EVENT_FOR_STATE.get(to_state)
    if with_reason is not None:
        return with_reason.value, {"pid": pid, "reason": reason or ""}
    if to_state is ProcessState.RUNNING:
        # WAITING/PAUSED -> RUNNING là resume, không phải lần chạy đầu (doc 05).
        resumed_from = from_state in (ProcessState.WAITING, ProcessState.PAUSED)
        event_type = EventType.PROCESS_RESUMED if resumed_from else EventType.PROCESS_STARTED
        return event_type.value, {"pid": pid}
    if to_state is ProcessState.SUCCEEDED:
        if ended_at is None:
            raise RuntimeError("ended_at phải được đặt trước khi tới SUCCEEDED — lỗi lập trình")
        return EventType.PROCESS_COMPLETED.value, {
            "pid": pid,
            "duration_ms": _duration_ms(started_at, ended_at),
        }
    if to_state is ProcessState.FAILED:
        return EventType.PROCESS_FAILED.value, {"pid": pid, "error_code": error_code or "INTERNAL"}
    if to_state is ProcessState.CANCELLED:
        return EventType.PROCESS_CANCELLED.value, {"pid": pid, "by": reason or "user"}
    raise RuntimeError(f"Trạng thái {to_state.value} chưa có đường vào — không thể tới đây")


class ProcessManager:
    def __init__(self, store: StateStore, events: EventBus) -> None:
        self._store = store
        self._events = events

    async def create(
        self, *, intent: str, spec: dict[str, Any], name: str, workflow_ref: str, priority: int = 5
    ) -> Process:
        async def _create(conn: aiosqlite.Connection) -> tuple[Process, Any]:
            now = clock.now().isoformat()
            job_id = ids.new_id("job")
            await conn.execute(
                "INSERT INTO jobs(job_id, intent, spec_json, priority, created_at, schema_version) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (job_id, intent, json.dumps(spec, ensure_ascii=False), priority, now),
            )

            pid_cursor = await conn.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'pid' RETURNING value"
            )
            pid_row = await pid_cursor.fetchone()
            if pid_row is None:
                raise RuntimeError("counters thiếu hàng 'pid' — migration 001 chưa chạy đúng")
            pid = int(pid_row[0])

            process_id = ids.new_id("proc")
            await conn.execute(
                # _PROCESS_COLUMNS là hằng số cố định, không phải input — S608 báo giả.
                f"INSERT INTO processes({_PROCESS_COLUMNS}, schema_version) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, 0, 0, NULL, NULL, NULL, NULL, 1)",
                (process_id, pid, job_id, name, workflow_ref, ProcessState.CREATED.value),
            )
            await conn.execute(
                "INSERT INTO process_transitions(process_id, from_state, to_state, reason, at) "
                "VALUES (?, NULL, ?, 'created', ?)",
                (process_id, ProcessState.CREATED.value, now),
            )
            envelope = await self._events.build_and_insert(
                conn,
                EventType.PROCESS_CREATED.value,
                source="kernel.process",
                payload={"pid": pid, "workflow_ref": workflow_ref, "spec": spec},
                process_id=process_id,
            )
            process = Process(
                process_id=process_id,
                pid=pid,
                job_id=job_id,
                name=name,
                workflow_ref=workflow_ref,
                state=ProcessState.CREATED,
                progress=0.0,
                checkpoint_seq=0,
                started_at=None,
                ended_at=None,
                error_code=None,
                error_json=None,
            )
            return process, envelope

        process, envelope = await self._store.write(_create)
        await self._events.dispatch(envelope)
        return process

    async def transition(
        self,
        process_id: str,
        to_state: ProcessState,
        *,
        reason: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Process:
        async def _transition(conn: aiosqlite.Connection) -> tuple[Process, Any]:
            cursor = await conn.execute(
                _select_processes_sql(" WHERE process_id = ?"), (process_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise PaosError(
                    ErrorCode.NOT_FOUND,
                    f"Process {process_id} không tồn tại",
                    hint="Kiểm lại process_id",
                    context={"process_id": process_id},
                )
            current = _row_to_process(row)
            allowed = _VALID_TRANSITIONS.get(current.state, frozenset())
            if to_state not in allowed:
                raise PaosError(
                    ErrorCode.CONFLICT,
                    f"Không thể chuyển {current.state.value} -> {to_state.value}",
                    hint=f"Trạng thái hợp lệ từ {current.state.value}: "
                    f"{sorted(s.value for s in allowed) or '(không còn — trạng thái cuối)'}",
                    context={
                        "process_id": process_id,
                        "from": current.state.value,
                        "to": to_state.value,
                    },
                )

            now = clock.now().isoformat()
            started_at = current.started_at
            ended_at = current.ended_at
            if to_state is ProcessState.RUNNING and started_at is None:
                started_at = now
            if to_state in (
                ProcessState.SUCCEEDED,
                ProcessState.FAILED,
                ProcessState.FAILED_FINAL,
                ProcessState.CANCELLED,
            ):
                ended_at = now

            error_json = json.dumps({"message": error_message}) if error_message else None
            await conn.execute(
                "UPDATE processes SET state=?, started_at=?, ended_at=?, "
                "error_code=?, error_json=? WHERE process_id=?",
                (to_state.value, started_at, ended_at, error_code, error_json, process_id),
            )
            await conn.execute(
                "INSERT INTO process_transitions(process_id, from_state, to_state, reason, at) "
                "VALUES (?, ?, ?, ?, ?)",
                (process_id, current.state.value, to_state.value, reason, now),
            )

            event_type, payload = _event_for_transition(
                current.state,
                to_state,
                current.pid,
                started_at=started_at,
                ended_at=ended_at,
                error_code=error_code,
                reason=reason,
            )
            envelope = await self._events.build_and_insert(
                conn,
                event_type,
                source="kernel.process",
                payload=payload,
                process_id=process_id,
            )

            updated = replace(
                current,
                state=to_state,
                started_at=started_at,
                ended_at=ended_at,
                error_code=error_code,
                error_json=error_json,
            )
            return updated, envelope

        process, envelope = await self._store.write(_transition)
        await self._events.dispatch(envelope)
        return process

    async def get(self, process_id: str) -> Process | None:
        async def _get(conn: aiosqlite.Connection) -> Process | None:
            cursor = await conn.execute(
                _select_processes_sql(" WHERE process_id = ?"), (process_id,)
            )
            row = await cursor.fetchone()
            return _row_to_process(row) if row is not None else None

        return await self._store.read(_get)

    async def get_by_pid(self, pid: int) -> Process | None:
        async def _get(conn: aiosqlite.Connection) -> Process | None:
            cursor = await conn.execute(_select_processes_sql(" WHERE pid = ?"), (pid,))
            row = await cursor.fetchone()
            return _row_to_process(row) if row is not None else None

        return await self._store.read(_get)

    async def list(self, *, state: ProcessState | None = None) -> list[Process]:
        async def _list(conn: aiosqlite.Connection) -> list[Process]:
            if state is None:
                cursor = await conn.execute(_select_processes_sql(" ORDER BY pid"))
            else:
                cursor = await conn.execute(
                    _select_processes_sql(" WHERE state = ? ORDER BY pid"), (state.value,)
                )
            return [_row_to_process(row) for row in await cursor.fetchall()]

        return await self._store.read(_list)
