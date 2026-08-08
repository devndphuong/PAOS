"""Event Bus — ghi trước khi dispatch, at-least-once, envelope chuẩn (doc 05, ADR-0003)."""

from __future__ import annotations

import asyncio
import fnmatch
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiosqlite
import jsonschema

from kernel import clock, ids
from kernel.errors import ErrorCode, PaosError
from kernel.state.db import StateStore

_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "events"


@dataclass(frozen=True)
class EventEnvelope:
    """Đủ 10 trường doc 05 §1 — kể cả correlation_id/causation_id dù M0 chưa dùng."""

    event_id: str
    type: str
    version: int
    ts: str
    source: str
    process_id: str | None
    task_id: str | None
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]


Subscriber = Callable[[EventEnvelope], Awaitable[None]]


class EventBus:
    """Durable-first: publish() ghi vào SQLite qua StateStore rồi mới dispatch (REL-01)."""

    def __init__(self, store: StateStore, schema_dir: Path = _DEFAULT_SCHEMA_DIR) -> None:
        self._store = store
        self._schema_dir = schema_dir
        self._subscribers: dict[str, tuple[str, Subscriber]] = {}

    def subscribe(self, name: str, pattern: str, handler: Subscriber) -> None:
        """`pattern` kiểu `agent.*.completed`, khớp bằng fnmatch (doc 05 §4)."""
        self._subscribers[name] = (pattern, handler)

    async def start(self) -> None:
        """Giao lại event chưa có bản ghi event_deliveries cho từng subscriber (REL-01)."""
        pending = await self._store.read(self._find_undelivered)
        for name, envelope in pending:
            handler = self._subscribers[name][1]
            await self._deliver_one(name, handler, envelope)

    async def stop(self) -> None:
        pass  # EventBus không giữ tài nguyên riêng — StateStore sở hữu kết nối

    async def publish(
        self,
        type: str,
        source: str,
        payload: dict[str, Any],
        *,
        version: int = 1,
        process_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventEnvelope:
        self._validate_payload(type, version, payload)

        async def _insert(conn: aiosqlite.Connection) -> EventEnvelope:
            cursor = await conn.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'event_seq' RETURNING value"
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("counters thiếu hàng 'event_seq' — migration 002 chưa chạy đúng")
            seq = int(row[0])
            envelope = EventEnvelope(
                event_id=ids.new_id("evt"),
                type=type,
                version=version,
                ts=clock.now().isoformat(),
                source=source,
                process_id=process_id,
                task_id=task_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                payload=payload,
            )
            await conn.execute(
                "INSERT INTO events(event_id, seq, type, version, ts, source, process_id, "
                "task_id, correlation_id, causation_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    envelope.event_id,
                    seq,
                    envelope.type,
                    envelope.version,
                    envelope.ts,
                    envelope.source,
                    envelope.process_id,
                    envelope.task_id,
                    envelope.correlation_id,
                    envelope.causation_id,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return envelope

        envelope = await self._store.write(_insert)
        await self._dispatch(envelope)
        return envelope

    async def _dispatch(self, envelope: EventEnvelope) -> None:
        for name, (pattern, handler) in self._subscribers.items():
            if fnmatch.fnmatch(envelope.type, pattern):
                await self._deliver_one(name, handler, envelope)

    async def _deliver_one(self, name: str, handler: Subscriber, envelope: EventEnvelope) -> None:
        try:
            await handler(envelope)
            state, last_error = "delivered", None
        except Exception as exc:  # subscriber lỗi không được làm chết Bus hay subscriber khác
            state, last_error = "failed", str(exc)

        async def _record(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT OR REPLACE INTO event_deliveries"
                "(event_id, subscriber, state, attempts, last_error) VALUES (?, ?, ?, 1, ?)",
                (envelope.event_id, name, state, last_error),
            )

        await self._store.write(_record)

    async def _find_undelivered(
        self, conn: aiosqlite.Connection
    ) -> list[tuple[str, EventEnvelope]]:
        result: list[tuple[str, EventEnvelope]] = []
        for name, (pattern, _handler) in self._subscribers.items():
            cursor = await conn.execute(
                "SELECT e.event_id, e.type, e.version, e.ts, e.source, e.process_id, e.task_id, "
                "e.correlation_id, e.causation_id, e.payload_json "
                "FROM events e LEFT JOIN event_deliveries d "
                "  ON d.event_id = e.event_id AND d.subscriber = ? "
                "WHERE d.event_id IS NULL ORDER BY e.seq",
                (name,),
            )
            for row in await cursor.fetchall():
                envelope = EventEnvelope(
                    event_id=row[0],
                    type=row[1],
                    version=row[2],
                    ts=row[3],
                    source=row[4],
                    process_id=row[5],
                    task_id=row[6],
                    correlation_id=row[7],
                    causation_id=row[8],
                    payload=json.loads(row[9]),
                )
                if fnmatch.fnmatch(envelope.type, pattern):
                    result.append((name, envelope))
        return result

    def _load_schema(self, type: str, version: int) -> dict[str, Any] | None:
        path = self._schema_dir / f"{type}.v{version}.schema.json"
        if not path.exists():
            return None
        schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return schema

    def _validate_payload(self, type: str, version: int, payload: dict[str, Any]) -> None:
        schema = self._load_schema(type, version)
        if schema is None:
            raise PaosError(
                ErrorCode.INVALID_INPUT,
                f"Không có schema đã đăng ký cho event '{type}' v{version}",
                hint=f"Thêm schemas/events/{type}.v{version}.schema.json trước khi phát event này",
                context={"type": type, "version": version},
            )
        try:
            jsonschema.validate(payload, schema)
        except jsonschema.ValidationError as exc:
            raise PaosError(
                ErrorCode.INVALID_INPUT,
                f"Payload event '{type}' không khớp schema: {exc.message}",
                hint="Kiểm payload theo đúng schemas/events/ tương ứng",
                context={"type": type, "version": version},
            ) from exc


def make_project_logger(log_path: Path) -> Subscriber:
    """Subscriber lõi ghi ndjson (doc 05 §4). M0: log toàn cục, chưa tách theo project
    (Process/Project chưa tồn tại tới lát cắt 3+) — nợ có chủ đích, ghi ở docs/backlog.md.
    """

    async def _handler(envelope: EventEnvelope) -> None:
        await asyncio.to_thread(_append_line, log_path, envelope)

    return _handler


def _append_line(log_path: Path, envelope: EventEnvelope) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(envelope), ensure_ascii=False) + "\n")
