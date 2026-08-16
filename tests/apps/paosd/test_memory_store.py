"""Kiểm MemoryStore (doc 03 §3, doc 07, P-M5-1) — DAO thuần, không I/O ngoài
`state.db`, cùng khuôn mẫu tests/apps/paosd/test_cache_store.py (nếu có) /
test_artifact_store thông qua build_daemon."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.paosd.memory_store import MemoryStore
from kernel.events.bus import EventBus
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def events(store: StateStore) -> EventBus:
    return EventBus(store)


@pytest.fixture
def memory_store(store: StateStore, events: EventBus) -> MemoryStore:
    return MemoryStore(store, events)


async def test_write_then_get_roundtrip(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="thích tone chuyên nghiệp", key="tone")
    got = await memory_store.get(item.memory_id)
    assert got is not None
    assert got.content == "thích tone chuyên nghiệp"
    assert got.tier == "L3"
    assert got.key == "tone"
    assert got.confidence == 0.7  # mặc định doc 03


async def test_get_unknown_id_returns_none(memory_store: MemoryStore) -> None:
    assert await memory_store.get("mem_khong_ton_tai") is None


async def test_get_by_key_finds_latest_when_duplicate_keys(memory_store: MemoryStore) -> None:
    await memory_store.write(tier="L3", content="bản cũ", key="tone")
    newest = await memory_store.write(tier="L3", content="bản mới", key="tone")
    found = await memory_store.get_by_key("L3", "tone")
    assert found is not None
    assert found.memory_id == newest.memory_id
    assert found.content == "bản mới"


async def test_get_by_key_unknown_returns_none(memory_store: MemoryStore) -> None:
    assert await memory_store.get_by_key("L3", "khong_ton_tai") is None


async def test_list_by_tier_filters_correctly(memory_store: MemoryStore) -> None:
    await memory_store.write(tier="L3", content="a", key="k1")
    await memory_store.write(tier="L3", content="b", key="k2")
    await memory_store.write(tier="L4", content="c", key="k3")

    l3_items = await memory_store.list_by_tier("L3")
    assert len(l3_items) == 2
    l4_items = await memory_store.list_by_tier("L4")
    assert len(l4_items) == 1


async def test_list_by_tier_filters_by_scope_id(memory_store: MemoryStore) -> None:
    await memory_store.write(tier="L2", content="a", scope_id="proj_1")
    await memory_store.write(tier="L2", content="b", scope_id="proj_2")

    scoped = await memory_store.list_by_tier("L2", scope_id="proj_1")
    assert len(scoped) == 1
    assert scoped[0].content == "a"


async def test_write_redacts_secret_looking_content(memory_store: MemoryStore) -> None:
    """doc 09 §6, SEC-01 — cùng bộ lọc đã dùng cho artifact/event."""
    item = await memory_store.write(
        tier="L4", content="API key: sk-abcdef1234567890 dùng để gọi provider"
    )
    assert "sk-abcdef1234567890" not in item.content
    assert "***REDACTED***" in item.content
    got = await memory_store.get(item.memory_id)
    assert got is not None
    assert "sk-abcdef1234567890" not in got.content


async def test_write_emits_memory_item_written_event(
    memory_store: MemoryStore, events: EventBus
) -> None:
    received = []

    async def _handler(envelope):  # type: ignore[no-untyped-def]
        received.append(envelope)

    events.subscribe("watcher", "memory.item.written", _handler)
    item = await memory_store.write(tier="L3", content="x", key="pref.x")

    assert len(received) == 1
    assert received[0].payload == {"memory_id": item.memory_id, "tier": "L3", "key": "pref.x"}


async def test_touch_updates_last_used_at_and_use_count(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="x", key="pref.x")
    assert item.last_used_at is None
    assert item.use_count == 0

    await memory_store.touch(item.memory_id)
    touched = await memory_store.get(item.memory_id)
    assert touched is not None
    assert touched.last_used_at is not None
    assert touched.use_count == 1

    await memory_store.touch(item.memory_id)
    touched_again = await memory_store.get(item.memory_id)
    assert touched_again is not None
    assert touched_again.use_count == 2


async def test_write_with_embedding_stores_vector_row(memory_store: MemoryStore) -> None:
    item = await memory_store.write(
        tier="L3", content="x", key="pref.x", embedding=([0.1, 0.2, 0.3], "stub.embed")
    )

    async def _select(conn):  # type: ignore[no-untyped-def]
        cursor = await conn.execute(
            "SELECT model, dim FROM memory_vectors WHERE memory_id = ?", (item.memory_id,)
        )
        return await cursor.fetchone()

    row = await memory_store._store.read(_select)
    assert row == ("stub.embed", 3)


async def test_update_confidence_updates_in_place_not_new_row(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="x", key="pref.x", confidence=0.3)
    await memory_store.update(item.memory_id, confidence=0.55)

    updated = await memory_store.get(item.memory_id)
    assert updated is not None
    assert updated.confidence == pytest.approx(0.55)
    assert updated.memory_id == item.memory_id  # cùng hàng, không tạo mới
    assert updated.content == "x"  # không đụng tới vì content=None

    all_items = await memory_store.list_by_tier("L3")
    assert len(all_items) == 1


async def test_update_content_and_scope_id(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="75", key="pref.x", scope_id="proc_1")
    await memory_store.update(item.memory_id, content="90", scope_id="proc_2")

    updated = await memory_store.get(item.memory_id)
    assert updated is not None
    assert updated.content == "90"
    assert updated.scope_id == "proc_2"
    assert updated.confidence == 0.7  # không đụng tới vì confidence=None


async def test_update_redacts_new_content(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="x", key="pref.x")
    await memory_store.update(item.memory_id, content="key=sk-abcdef1234567890")

    updated = await memory_store.get(item.memory_id)
    assert updated is not None
    assert "sk-abcdef1234567890" not in updated.content


async def test_update_with_no_fields_is_a_noop(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="x", key="pref.x")
    await memory_store.update(item.memory_id)

    unchanged = await memory_store.get(item.memory_id)
    assert unchanged is not None
    assert unchanged.content == "x"
    assert unchanged.confidence == 0.7


# --- forget() — doc 07 §6, ADR-0029 (xóa cứng thật, ngoại lệ ADR-0012) -----


async def test_forget_hard_deletes_row_not_just_marks_it(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="riêng tư", key="pref.x")
    ok = await memory_store.forget(item.memory_id)

    assert ok is True
    assert await memory_store.get(item.memory_id) is None
    remaining = await memory_store.list_by_tier("L3")
    assert item.memory_id not in [i.memory_id for i in remaining]


async def test_forget_also_deletes_vector_row(memory_store: MemoryStore) -> None:
    item = await memory_store.write(
        tier="L3", content="x", key="pref.x", embedding=([0.1, 0.2], "stub.embed")
    )
    await memory_store.forget(item.memory_id)

    async def _count(conn):  # type: ignore[no-untyped-def]
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM memory_vectors WHERE memory_id = ?", (item.memory_id,)
        )
        row = await cursor.fetchone()
        return row[0]

    assert await memory_store._store.read(_count) == 0


async def test_forget_unknown_id_returns_false(memory_store: MemoryStore) -> None:
    assert await memory_store.forget("mem_khong_ton_tai") is False


async def test_forget_emits_event_without_content(
    memory_store: MemoryStore, events: EventBus
) -> None:
    received = []

    async def _handler(envelope):  # type: ignore[no-untyped-def]
        received.append(envelope)

    events.subscribe("watcher", "memory.item.forgotten", _handler)
    item = await memory_store.write(tier="L3", content="bí mật cá nhân", key="pref.x")
    await memory_store.forget(item.memory_id)

    assert len(received) == 1
    assert received[0].payload == {"memory_id": item.memory_id, "tier": "L3", "key": "pref.x"}
    assert "content" not in received[0].payload
    assert "bí mật cá nhân" not in str(received[0].payload)


async def test_forget_twice_is_idempotent(memory_store: MemoryStore) -> None:
    item = await memory_store.write(tier="L3", content="x", key="pref.x")
    assert await memory_store.forget(item.memory_id) is True
    assert await memory_store.forget(item.memory_id) is False  # đã mất, không lỗi


# --- export_json()/import_json() — doc 07 §6 "tự soi và tự sửa" ------------


async def test_export_json_includes_all_tiers(memory_store: MemoryStore) -> None:
    await memory_store.write(tier="L3", content="a", key="k1")
    await memory_store.write(tier="L4", content="b", key="k2")

    exported = await memory_store.export_json()

    assert len(exported) == 2
    tiers = {e["tier"] for e in exported}
    assert tiers == {"L3", "L4"}
    assert all("content" in e and "confidence" in e for e in exported)


async def test_import_json_creates_new_rows(memory_store: MemoryStore) -> None:
    count = await memory_store.import_json(
        [
            {"tier": "L3", "content": "nhập từ file", "key": "pref.y", "confidence": 0.6},
        ]
    )
    assert count == 1
    found = await memory_store.get_by_key("L3", "pref.y")
    assert found is not None
    assert found.content == "nhập từ file"
    assert found.confidence == pytest.approx(0.6)


async def test_import_json_with_existing_memory_id_updates_in_place(
    memory_store: MemoryStore,
) -> None:
    item = await memory_store.write(tier="L3", content="cũ", key="pref.z", confidence=0.3)

    count = await memory_store.import_json(
        [{"memory_id": item.memory_id, "content": "đã tự sửa tay", "confidence": 0.9}]
    )

    assert count == 1
    updated = await memory_store.get(item.memory_id)
    assert updated is not None
    assert updated.content == "đã tự sửa tay"
    assert updated.confidence == pytest.approx(0.9)
    all_l3 = await memory_store.list_by_tier("L3")
    assert len(all_l3) == 1  # cập nhật tại chỗ, không tạo hàng mới


async def test_import_json_redacts_secret_content(memory_store: MemoryStore) -> None:
    await memory_store.import_json(
        [{"tier": "L4", "content": "key=sk-abcdef1234567890", "key": "leak"}]
    )
    found = await memory_store.get_by_key("L4", "leak")
    assert found is not None
    assert "sk-abcdef1234567890" not in found.content
    assert "***REDACTED***" in found.content


async def test_export_then_import_roundtrip(memory_store: MemoryStore) -> None:
    await memory_store.write(tier="L3", content="a", key="k1", confidence=0.55)
    exported = await memory_store.export_json()

    # Xóa hết rồi nhập lại từ bản xuất — mô phỏng "tự soi rồi tự sửa lại".
    for e in exported:
        await memory_store.forget(e["memory_id"])
    assert await memory_store.list_by_tier("L3") == []

    imported_count = await memory_store.import_json(exported)
    assert imported_count == len(exported)
    restored = await memory_store.get_by_key("L3", "k1")
    assert restored is not None
    assert restored.content == "a"
    assert restored.confidence == pytest.approx(0.55)
