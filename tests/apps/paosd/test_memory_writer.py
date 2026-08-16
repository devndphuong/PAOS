"""Kiểm MemoryWriter (doc 07 §2, P-M5-2) — quan sát Event thật (EventEnvelope
dựng tay, không cần build_daemon đầy đủ — kịch bản HTTP end-to-end đã kiểm ở
tests/apps/paosd/test_app.py qua build_daemon thật)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.paosd.memory_store import MemoryStore
from apps.paosd.memory_writer import MemoryWriter
from kernel.events.bus import EventBus, EventEnvelope
from kernel.state.db import StateStore
from sdk.preference import INITIAL_CANDIDATE_CONFIDENCE


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


@pytest.fixture
def writer(memory_store: MemoryStore) -> MemoryWriter:
    return MemoryWriter(memory_store)


def _process_created(process_id: str, spec: dict, seq: int = 0) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{seq}",
        seq=seq,
        type="kernel.process.created",
        version=1,
        ts="2026-08-16T00:00:00",
        source="kernel.process",
        process_id=process_id,
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"pid": seq, "workflow_ref": "wf@1", "spec": spec, "priority": 5},
    )


def _artifact_edited(process_id: str, edit_rate: float = 0.3, seq: int = 100) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{seq}",
        seq=seq,
        type="quality.artifact.edited",
        version=1,
        ts="2026-08-16T00:00:00",
        source="paosd",
        process_id=process_id,
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"artifact_id": "art_x", "edited_artifact_id": "art_y", "edit_rate": edit_rate},
    )


async def test_first_observation_creates_candidate_with_initial_confidence(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", {"text": "x", "duration_sec": 75}))

    item = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert item is not None
    assert item.content == "75"
    assert item.confidence == pytest.approx(INITIAL_CANDIDATE_CONFIDENCE)
    assert item.scope_id == "proc_1"


async def test_non_preference_spec_keys_are_ignored(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", {"text": "nội dung", "plan": "y"}))
    assert await memory_store.get_by_key("L3", "pref.text") is None
    assert await memory_store.list_by_tier("L3") == []


async def test_repeated_same_value_reinforces_confidence(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    for i, pid in enumerate(["proc_1", "proc_2", "proc_3"]):
        await writer.on_process_created(_process_created(pid, {"duration_sec": 75}, seq=i))

    item = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert item is not None
    # 0.3 (tạo) + 0.10 + 0.10 (2 lần củng cố) = 0.5
    assert item.confidence == pytest.approx(0.5)
    assert item.scope_id == "proc_3"  # re-scope theo lần quan sát gần nhất


async def test_contradiction_on_weak_candidate_replaces_value(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", {"duration_sec": 75}, seq=0))
    item_before = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert item_before is not None
    assert item_before.confidence == pytest.approx(0.3)  # < ngưỡng 0.4 -> còn yếu

    await writer.on_process_created(_process_created("proc_2", {"duration_sec": 90}, seq=1))
    item_after = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert item_after is not None
    assert item_after.content == "90"  # bị thay hẳn vì còn quá yếu
    assert item_after.confidence == pytest.approx(INITIAL_CANDIDATE_CONFIDENCE)
    assert item_after.scope_id == "proc_2"


async def test_contradiction_on_strong_candidate_keeps_value_but_lowers_confidence(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    for i, pid in enumerate(["proc_1", "proc_2", "proc_3"]):
        await writer.on_process_created(_process_created(pid, {"duration_sec": 75}, seq=i))
    strong = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert strong is not None
    assert strong.confidence == pytest.approx(0.5)  # >= ngưỡng 0.4 -> đủ vững

    await writer.on_process_created(_process_created("proc_4", {"duration_sec": 90}, seq=3))
    after = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert after is not None
    assert after.content == "75"  # GIỮ giá trị cũ
    assert after.confidence == pytest.approx(0.35)  # 0.5 - 0.15 (CONTRADICTED)
    assert after.scope_id == "proc_3"  # KHÔNG re-scope khi chỉ hạ confidence


async def test_artifact_edited_demotes_preferences_scoped_to_that_process(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(
        _process_created("proc_1", {"duration_sec": 75, "tone": "professional"})
    )
    before_duration = await memory_store.get_by_key("L3", "pref.duration_sec")
    before_tone = await memory_store.get_by_key("L3", "pref.tone")
    assert before_duration is not None
    assert before_tone is not None

    await writer.on_artifact_edited(_artifact_edited("proc_1"))

    after_duration = await memory_store.get_by_key("L3", "pref.duration_sec")
    after_tone = await memory_store.get_by_key("L3", "pref.tone")
    assert after_duration is not None
    assert after_tone is not None
    assert after_duration.confidence == pytest.approx(max(0.0, before_duration.confidence - 0.40))
    assert after_tone.confidence == pytest.approx(max(0.0, before_tone.confidence - 0.40))


async def test_artifact_edited_for_unrelated_process_does_not_touch_other_preferences(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", {"duration_sec": 75}))
    before = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert before is not None

    await writer.on_artifact_edited(_artifact_edited("proc_unrelated"))

    after = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert after is not None
    assert after.confidence == before.confidence  # không đổi


async def test_confidence_clamps_at_zero_after_repeated_corrections(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", {"duration_sec": 75}))
    for _ in range(5):
        await writer.on_artifact_edited(_artifact_edited("proc_1"))

    item = await memory_store.get_by_key("L3", "pref.duration_sec")
    assert item is not None
    assert item.confidence == 0.0


async def test_process_created_without_process_id_is_ignored(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    envelope = EventEnvelope(
        event_id="evt_x",
        seq=0,
        type="kernel.process.created",
        version=1,
        ts="2026-08-16T00:00:00",
        source="kernel.process",
        process_id=None,
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"spec": {"duration_sec": 75}},
    )
    await writer.on_process_created(envelope)
    assert await memory_store.list_by_tier("L3") == []


async def test_process_created_without_spec_dict_is_ignored(
    writer: MemoryWriter, memory_store: MemoryStore
) -> None:
    await writer.on_process_created(_process_created("proc_1", spec=None))  # type: ignore[arg-type]
    assert await memory_store.list_by_tier("L3") == []
