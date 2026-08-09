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
    return EventBus(store, max_retries=2)  # nhỏ để test nhanh, không đổi hành vi


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


async def test_subscriber_exhausting_retries_goes_to_dead_letter_others_unaffected(
    bus: EventBus, store: StateStore
) -> None:
    received = []
    bad_attempts = 0

    async def _bad(envelope: EventEnvelope) -> None:
        nonlocal bad_attempts
        bad_attempts += 1
        raise ValueError("boom")

    async def _good(envelope: EventEnvelope) -> None:
        received.append(envelope)

    bus.subscribe("bad", "kernel.*", _bad)
    bus.subscribe("good", "kernel.*", _good)

    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})

    assert len(received) == 1  # subscriber tốt không bị ảnh hưởng bởi subscriber lỗi
    assert bad_attempts == 2  # đúng max_retries=2 của fixture `bus`

    async def _delivery_rows(conn: aiosqlite.Connection) -> dict[str, tuple[str, int]]:
        cursor = await conn.execute("SELECT subscriber, state, attempts FROM event_deliveries")
        return {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}

    rows = await store.read(_delivery_rows)
    assert rows == {"bad": ("dead_letter", 2), "good": ("delivered", 1)}

    dead_letters = await bus.dead_letters()
    assert len(dead_letters) == 1
    assert dead_letters[0]["subscriber"] == "bad"
    assert dead_letters[0]["type"] == "kernel.startup"
    assert dead_letters[0]["attempts"] == 2
    assert "boom" in dead_letters[0]["last_error"]


async def test_subscriber_succeeding_on_retry_does_not_reach_dead_letter(
    bus: EventBus, store: StateStore
) -> None:
    attempts = 0

    async def _flaky(envelope: EventEnvelope) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ValueError("thoáng qua")

    bus.subscribe("flaky", "kernel.*", _flaky)
    await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})

    assert attempts == 2

    async def _delivery_row(conn: aiosqlite.Connection) -> tuple[str, int]:
        cursor = await conn.execute(
            "SELECT state, attempts FROM event_deliveries WHERE subscriber = 'flaky'"
        )
        row = await cursor.fetchone()
        assert row is not None
        return row[0], row[1]

    state, recorded_attempts = await store.read(_delivery_row)
    assert state == "delivered"
    assert recorded_attempts == 2  # thành công ở lần thử thứ 2, không vào DLQ

    assert await bus.dead_letters() == []


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


class _Projection:
    """Subscriber tối giản, thuần đếm theo loại — đại diện cho lớp subscriber
    "dựng trạng thái phái sinh từ event" (Memory/KG thật là M5, chưa tồn tại
    ở M1). Idempotent theo yêu cầu doc 19 P-M1-5b: chỉ CỘNG DỒN đếm, gọi lại
    với cùng envelope vẫn tăng đúng — bài test không đòi khử trùng lặp ở đây,
    chỉ đòi "replay từ đầu == dựng dần" khi mỗi event chỉ tới đúng 1 lần."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def handle(self, envelope: EventEnvelope) -> None:
        self.counts[envelope.type] = self.counts.get(envelope.type, 0) + 1


async def test_replay_full_range_matches_incrementally_built_state(
    tmp_path: Path,
) -> None:
    """Test bắt buộc doc 19 P-M1-5b: replay toàn bộ event từ đầu phải cho ra
    đúng trạng thái phái sinh giống hệt bản dựng dần lúc event tới trực tiếp."""
    store = StateStore(tmp_path / ".paos" / "state.db")
    await store.start()
    try:
        bus = EventBus(store)
        live_projection = _Projection()
        bus.subscribe("projection", "kernel.*", live_projection.handle)

        # Dựng DẦN — mỗi publish() dispatch ngay, live_projection nhận trực tiếp.
        for i in range(5):
            await bus.publish("kernel.startup", "test.source", {"version": f"0.0.{i}"})
        for i in range(3):
            await bus.publish("kernel.shutdown", "test.source", {"version": "0.0.1", "uptime": i})

        assert live_projection.counts == {"kernel.startup": 5, "kernel.shutdown": 3}

        # Dựng LẠI TỪ ĐẦU bằng replay() trên một projection MỚI, cùng subscriber
        # name (đăng ký handler khác, độc lập bộ đếm).
        replayed_projection = _Projection()
        bus.subscribe("projection", "kernel.*", replayed_projection.handle)

        count = await bus.replay("0001-01-01T00:00:00", "9999-12-31T23:59:59", "projection")

        assert count == 8
        assert replayed_projection.counts == live_projection.counts
    finally:
        await store.stop()


async def test_replay_respects_subscriber_pattern(tmp_path: Path) -> None:
    store = StateStore(tmp_path / ".paos" / "state.db")
    await store.start()
    try:
        bus = EventBus(store)
        await bus.publish("kernel.startup", "test.source", {"version": "0.0.1"})
        await bus.publish("kernel.shutdown", "test.source", {"version": "0.0.1", "uptime": 1})

        projection = _Projection()
        bus.subscribe("startup_only", "kernel.startup", projection.handle)

        count = await bus.replay("0001-01-01T00:00:00", "9999-12-31T23:59:59", "startup_only")

        assert count == 1
        assert projection.counts == {"kernel.startup": 1}
    finally:
        await store.stop()


async def test_replay_unknown_subscriber_raises_not_found(bus: EventBus) -> None:
    with pytest.raises(PaosError) as exc_info:
        await bus.replay("0001-01-01T00:00:00", "9999-12-31T23:59:59", "chua_dang_ky")
    assert exc_info.value.code == ErrorCode.NOT_FOUND
