"""Kiểm EventBus — durable-first, dispatch, catch-up sau crash (doc 05, REL-01)."""

from pathlib import Path

import aiosqlite
import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope, make_project_logger
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def bus(store: StateStore) -> EventBus:
    return EventBus(store)


async def test_publish_writes_before_dispatch(bus: EventBus, store: StateStore) -> None:
    seen: list[EventEnvelope] = []

    async def _handler(envelope: EventEnvelope) -> None:
        # Lúc handler chạy, event PHẢI đã nằm trong DB (ghi trước, dispatch sau).
        async def _exists(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (envelope.event_id,)
            )
            return await cursor.fetchone() is not None

        assert await store.read(_exists)
        seen.append(envelope)

    bus.subscribe("test", "kernel.*", _handler)
    envelope = await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})

    assert seen == [envelope]


async def test_publish_validates_payload_against_schema(bus: EventBus, store: StateStore) -> None:
    with pytest.raises(PaosError) as exc_info:
        await bus.publish("kernel.startup", "test.source", {"wrong_field": 1})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT

    async def _count(conn: aiosqlite.Connection) -> int:
        cursor = await conn.execute("SELECT COUNT(*) FROM events")
        row = await cursor.fetchone()
        assert row is not None
        return int(row[0])

    assert await store.read(_count) == 0


async def test_publish_unregistered_type_is_invalid_input(bus: EventBus) -> None:
    with pytest.raises(PaosError) as exc_info:
        await bus.publish("no.such.event", "test.source", {})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


async def test_subscriber_matching_pattern_receives(bus: EventBus) -> None:
    received = []

    async def _handler(envelope: EventEnvelope) -> None:
        received.append(envelope)

    bus.subscribe("watcher", "kernel.*", _handler)
    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})
    assert len(received) == 1
    assert received[0].type == "kernel.startup"


async def test_subscriber_not_matching_pattern_ignored(bus: EventBus) -> None:
    received = []

    async def _handler(envelope: EventEnvelope) -> None:
        received.append(envelope)

    bus.subscribe("watcher", "agent.*", _handler)
    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})
    assert received == []


async def test_subscriber_exception_does_not_crash_bus_or_other_subscribers(
    bus: EventBus, store: StateStore
) -> None:
    received = []

    async def _bad(envelope: EventEnvelope) -> None:
        raise ValueError("boom")

    async def _good(envelope: EventEnvelope) -> None:
        received.append(envelope)

    bus.subscribe("bad", "kernel.*", _bad)
    bus.subscribe("good", "kernel.*", _good)

    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})

    assert len(received) == 1

    async def _delivery_states(conn: aiosqlite.Connection) -> dict[str, str]:
        cursor = await conn.execute("SELECT subscriber, state FROM event_deliveries")
        return dict(await cursor.fetchall())

    states = await store.read(_delivery_states)
    assert states == {"bad": "failed", "good": "delivered"}


async def test_startup_catchup_redelivers_undelivered_events(tmp_path: Path) -> None:
    db_path = tmp_path / ".paos" / "state.db"

    # Mô phỏng crash: publish thẳng vào DB mà không qua dispatch (không có subscriber nào).
    store1 = StateStore(db_path)
    await store1.start()
    bus1 = EventBus(store1)
    envelope = await bus1.publish("kernel.startup", "test.source", {"version": "0.0.1"})
    await store1.stop()

    # "Khởi động lại": subscriber MỚI đăng ký, chưa từng nhận event trên — catch-up phải giao lại.
    store2 = StateStore(db_path)
    await store2.start()
    try:
        bus2 = EventBus(store2)
        received = []

        async def _handler(e: EventEnvelope) -> None:
            received.append(e)

        bus2.subscribe("late_subscriber", "kernel.*", _handler)
        await bus2.start()

        assert len(received) == 1
        assert received[0].event_id == envelope.event_id
    finally:
        await store2.stop()


async def test_project_logger_writes_ndjson(bus: EventBus, tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "events.ndjson"
    bus.subscribe("project_logger", "*", make_project_logger(log_path))

    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})

    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"type": "kernel.startup"' in lines[0]
