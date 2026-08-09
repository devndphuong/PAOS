"""Event Bus — ghi trước khi dispatch, at-least-once, envelope chuẩn (doc 05, ADR-0003)."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import random
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
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE_S = 0.05
_BACKOFF_JITTER_S = 0.03


@dataclass(frozen=True)
class EventEnvelope:
    """Đủ 10 trường doc 05 §1 — kể cả correlation_id/causation_id dù M0 chưa dùng.
    `seq` KHÔNG nằm trong 10 trường đó (thứ tự ghi DB, chi tiết nội bộ) — thêm ở
    lát cắt 5c vì `events tail`/`explain` cần một con trỏ để phân trang/tail."""

    event_id: str
    seq: int
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


def _row_to_envelope(row: aiosqlite.Row | tuple[Any, ...]) -> EventEnvelope:
    return EventEnvelope(
        event_id=row[0],
        seq=row[1],
        type=row[2],
        version=row[3],
        ts=row[4],
        source=row[5],
        process_id=row[6],
        task_id=row[7],
        correlation_id=row[8],
        causation_id=row[9],
        payload=json.loads(row[10]),
    )


class EventBus:
    """Durable-first: publish() ghi vào SQLite qua StateStore rồi mới dispatch (REL-01)."""

    def __init__(
        self,
        store: StateStore,
        schema_dir: Path = _DEFAULT_SCHEMA_DIR,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self._store = store
        self._schema_dir = schema_dir
        self._subscribers: dict[str, tuple[str, Subscriber]] = {}
        self._max_retries = max_retries

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
        """Tiện ích cho trường hợp publish 1 event độc lập, không kèm ghi nào khác.
        Nếu cần ghi event CÙNG transaction với thay đổi khác (vd process_transitions,
        doc 19 P-M0-3), dùng build_and_insert() bên trong write() của caller rồi tự
        gọi dispatch() sau khi transaction của caller commit.
        """

        async def _insert(conn: aiosqlite.Connection) -> EventEnvelope:
            return await self.build_and_insert(
                conn,
                type,
                source,
                payload,
                version=version,
                process_id=process_id,
                task_id=task_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        envelope = await self._store.write(_insert)
        await self.dispatch(envelope)
        return envelope

    async def build_and_insert(
        self,
        conn: aiosqlite.Connection,
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
        """Validate + ghi event vào `conn` — transaction ĐANG MỞ của caller (bên trong
        một hàm truyền cho StateStore.write()). KHÔNG dispatch — gọi dispatch() sau khi
        transaction của caller commit thành công.
        """
        self._validate_payload(type, version, payload)
        cursor = await conn.execute(
            "UPDATE counters SET value = value + 1 WHERE name = 'event_seq' RETURNING value"
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("counters thiếu hàng 'event_seq' — migration 002 chưa chạy đúng")
        seq = int(row[0])
        envelope = EventEnvelope(
            event_id=ids.new_id("evt"),
            seq=seq,
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

    async def dispatch(self, envelope: EventEnvelope) -> None:
        """Dispatch một envelope đã ghi DB rồi — dùng sau build_and_insert() bên ngoài
        transaction của caller (hoặc nội bộ bởi publish())."""
        await self._dispatch(envelope)

    async def _dispatch(self, envelope: EventEnvelope) -> None:
        for name, (pattern, handler) in self._subscribers.items():
            if fnmatch.fnmatch(envelope.type, pattern):
                await self._deliver_one(name, handler, envelope)

    async def _deliver_one(self, name: str, handler: Subscriber, envelope: EventEnvelope) -> None:
        """Thử tối đa `max_retries` lần, chờ backoff mũ + jitter giữa các lần lỗi
        (doc 02 §3.3, doc 19 — trả nợ BL-001). Retry ĐỒNG BỘ ngay trong dispatch(),
        chưa phải hàng đợi nền riêng: chỉ có 1 subscriber thật (`runner`), lỗi của
        nó là bug lập trình chứ không phải lỗi thoáng qua kiểu mạng — xây scheduler
        nền riêng ở M1 là trừu tượng hoá sớm (P4), backoff đủ ngắn để không treo
        HTTP request lâu trên đường lỗi. Hết retry -> `dead_letter` (BL-002), khác
        `failed` ở chỗ: đây là "đã bỏ cuộc, cần người can thiệp" (`events replay`,
        M1-5b), không chỉ 1 lần thử không may."""
        last_error: str | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                await handler(envelope)
            except Exception as exc:  # subscriber lỗi không được làm chết Bus hay subscriber khác
                last_error = str(exc)
                if attempt < self._max_retries:
                    backoff = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                    jitter = random.uniform(0, _BACKOFF_JITTER_S)  # noqa: S311 — jitter thời gian chờ, không phải bảo mật
                    await asyncio.sleep(backoff + jitter)
            else:
                await self._record_delivery(envelope.event_id, name, "delivered", attempt, None)
                return

        await self._record_delivery(
            envelope.event_id, name, "dead_letter", self._max_retries, last_error
        )

    async def _record_delivery(
        self, event_id: str, subscriber: str, state: str, attempts: int, last_error: str | None
    ) -> None:
        async def _record(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT OR REPLACE INTO event_deliveries"
                "(event_id, subscriber, state, attempts, last_error) VALUES (?, ?, ?, ?, ?)",
                (event_id, subscriber, state, attempts, last_error),
            )

        await self._store.write(_record)

    async def dead_letters(self) -> list[dict[str, Any]]:
        """Event đã hết `max_retries` cho một subscriber cụ thể — cần
        `events replay` (M1-5b) để giao lại thủ công."""

        async def _query(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
            cursor = await conn.execute(
                "SELECT d.event_id, d.subscriber, d.attempts, d.last_error, "
                "e.type, e.ts, e.process_id "
                "FROM event_deliveries d JOIN events e ON e.event_id = d.event_id "
                "WHERE d.state = 'dead_letter' ORDER BY e.seq"
            )
            return [
                {
                    "event_id": row[0],
                    "subscriber": row[1],
                    "attempts": row[2],
                    "last_error": row[3],
                    "type": row[4],
                    "ts": row[5],
                    "process_id": row[6],
                }
                for row in await cursor.fetchall()
            ]

        return await self._store.read(_query)

    async def replay(self, from_ts: str, to_ts: str, to_subscriber: str) -> int:
        """Giao lại toàn bộ event trong khoảng `[from_ts, to_ts]` cho MỘT
        subscriber đã đăng ký (doc 02 §3.3, doc 19 P-M1-5b — trả nợ BL-002).
        Dùng chung logic retry/backoff/DLQ với dispatch bình thường (`_deliver_one`)
        — replay chỉ khác ở chỗ chủ động giao lại thay vì đợi event mới tới.

        Ràng buộc (doc 19): subscriber PHẢI chịu được việc dựng lại từ đầu —
        đây là điều kiện để M5 rebuild Memory/KG bằng chính cơ chế này, không
        phải cơ chế riêng."""
        if to_subscriber not in self._subscribers:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Subscriber '{to_subscriber}' chưa đăng ký",
                hint="Chỉ replay được tới subscriber đang chạy trong daemon hiện tại",
                context={"to_subscriber": to_subscriber},
            )
        pattern, handler = self._subscribers[to_subscriber]

        async def _query(conn: aiosqlite.Connection) -> list[EventEnvelope]:
            cursor = await conn.execute(
                "SELECT event_id, seq, type, version, ts, source, process_id, task_id, "
                "correlation_id, causation_id, payload_json FROM events "
                "WHERE ts >= ? AND ts <= ? ORDER BY seq",
                (from_ts, to_ts),
            )
            return [_row_to_envelope(row) for row in await cursor.fetchall()]

        envelopes = await self._store.read(_query)
        matching = [e for e in envelopes if fnmatch.fnmatch(e.type, pattern)]
        for envelope in matching:
            await self._deliver_one(to_subscriber, handler, envelope)
        return len(matching)

    async def _find_undelivered(
        self, conn: aiosqlite.Connection
    ) -> list[tuple[str, EventEnvelope]]:
        result: list[tuple[str, EventEnvelope]] = []
        for name, (pattern, _handler) in self._subscribers.items():
            cursor = await conn.execute(
                "SELECT e.event_id, e.seq, e.type, e.version, e.ts, e.source, e.process_id, "
                "e.task_id, e.correlation_id, e.causation_id, e.payload_json "
                "FROM events e LEFT JOIN event_deliveries d "
                "  ON d.event_id = e.event_id AND d.subscriber = ? "
                "WHERE d.event_id IS NULL ORDER BY e.seq",
                (name,),
            )
            for row in await cursor.fetchall():
                envelope = _row_to_envelope(row)
                if fnmatch.fnmatch(envelope.type, pattern):
                    result.append((name, envelope))
        return result

    async def events_for_process(self, process_id: str) -> list[EventEnvelope]:
        """Toàn bộ event của một process, theo đúng thứ tự xảy ra — nền của
        `paosctl explain` (doc 19 P-M0-5): dựng HOÀN TOÀN từ bảng events, không
        đọc bộ nhớ tiến trình sống (R17)."""

        async def _query(conn: aiosqlite.Connection) -> list[EventEnvelope]:
            cursor = await conn.execute(
                "SELECT event_id, seq, type, version, ts, source, process_id, task_id, "
                "correlation_id, causation_id, payload_json FROM events "
                "WHERE process_id = ? ORDER BY seq",
                (process_id,),
            )
            return [_row_to_envelope(row) for row in await cursor.fetchall()]

        return await self._store.read(_query)

    async def events_since(
        self, since_seq: int = 0, process_id: str | None = None
    ) -> list[EventEnvelope]:
        """Event có seq > since_seq, lọc thêm theo process_id nếu có — nền của
        `paosctl events tail`."""

        async def _query(conn: aiosqlite.Connection) -> list[EventEnvelope]:
            sql = (
                "SELECT event_id, seq, type, version, ts, source, process_id, task_id, "
                "correlation_id, causation_id, payload_json FROM events WHERE seq > ?"
            )
            params: list[Any] = [since_seq]
            if process_id is not None:
                sql += " AND process_id = ?"
                params.append(process_id)
            sql += " ORDER BY seq"
            cursor = await conn.execute(sql, params)
            return [_row_to_envelope(row) for row in await cursor.fetchall()]

        return await self._store.read(_query)

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
