"""Kiểm ProcessManager — máy trạng thái, transition+event cùng transaction
(doc 19 P-M0-3, mở rộng đầy đủ ở P-M1-1)."""

from pathlib import Path

import aiosqlite
import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.process.manager import _VALID_TRANSITIONS, Process, ProcessManager, ProcessState
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def manager(store: StateStore) -> ProcessManager:
    return ProcessManager(store, EventBus(store))


async def _create(manager: ProcessManager, name: str = "test") -> Process:
    return await manager.create(intent="test.intent", spec={}, name=name, workflow_ref="wf@1")


async def test_create_assigns_sequential_pid_in_same_txn(manager: ProcessManager) -> None:
    a = await _create(manager)
    b = await _create(manager)
    assert b.pid == a.pid + 1
    assert a.pid >= 1001


async def test_create_starts_in_created_state_emits_created_event(
    manager: ProcessManager, store: StateStore
) -> None:
    received: list[EventEnvelope] = []
    events = EventBus(store)
    mgr = ProcessManager(store, events)

    async def _handler(e: EventEnvelope) -> None:
        received.append(e)

    events.subscribe("watcher", "kernel.process.created", _handler)
    process = await mgr.create(intent="test.intent", spec={}, name="x", workflow_ref="wf@1")

    assert process.state is ProcessState.CREATED
    assert len(received) == 1
    assert received[0].payload == {
        "pid": process.pid,
        "workflow_ref": "wf@1",
        "spec": {},
        "priority": 5,
    }


async def test_full_chain_created_planning_queued_running_succeeded(
    manager: ProcessManager,
) -> None:
    p = await _create(manager)
    p = await manager.transition(p.process_id, ProcessState.PLANNING)
    assert p.state is ProcessState.PLANNING

    p = await manager.transition(p.process_id, ProcessState.QUEUED)
    assert p.state is ProcessState.QUEUED

    p = await manager.transition(p.process_id, ProcessState.RUNNING)
    assert p.state is ProcessState.RUNNING
    assert p.started_at is not None

    p = await manager.transition(p.process_id, ProcessState.SUCCEEDED)
    assert p.state is ProcessState.SUCCEEDED
    assert p.ended_at is not None


async def test_created_cannot_skip_planning_to_queued(manager: ProcessManager) -> None:
    p = await _create(manager)
    with pytest.raises(PaosError) as exc_info:
        await manager.transition(p.process_id, ProcessState.QUEUED)
    assert exc_info.value.code == ErrorCode.CONFLICT


async def test_invalid_transition_raises_conflict(manager: ProcessManager) -> None:
    p = await _create(manager)
    p = await manager.transition(p.process_id, ProcessState.PLANNING)
    p = await manager.transition(p.process_id, ProcessState.QUEUED)
    p = await manager.transition(p.process_id, ProcessState.RUNNING)
    p = await manager.transition(p.process_id, ProcessState.SUCCEEDED)

    with pytest.raises(PaosError) as exc_info:
        await manager.transition(p.process_id, ProcessState.RUNNING)
    assert exc_info.value.code == ErrorCode.CONFLICT


@pytest.mark.parametrize(
    "path_to_state",
    [
        [],
        [ProcessState.PLANNING],
        [ProcessState.PLANNING, ProcessState.QUEUED],
        [ProcessState.PLANNING, ProcessState.QUEUED, ProcessState.RUNNING],
        [ProcessState.PLANNING, ProcessState.QUEUED, ProcessState.RUNNING, ProcessState.WAITING],
        [ProcessState.PLANNING, ProcessState.QUEUED, ProcessState.RUNNING, ProcessState.PAUSED],
    ],
)
async def test_cancel_allowed_from_every_non_terminal_state(
    manager: ProcessManager, path_to_state: list[ProcessState]
) -> None:
    p = await _create(manager)
    for state in path_to_state:
        p = await manager.transition(p.process_id, state)
    p = await manager.transition(p.process_id, ProcessState.CANCELLED, reason="user_requested")
    assert p.state is ProcessState.CANCELLED


@pytest.mark.parametrize(
    "terminal",
    [ProcessState.SUCCEEDED, ProcessState.FAILED_FINAL, ProcessState.CANCELLED],
)
async def test_terminal_states_have_no_outgoing_transitions(terminal: ProcessState) -> None:
    assert _VALID_TRANSITIONS[terminal] == frozenset()


async def test_failed_is_no_longer_a_dead_end(manager: ProcessManager) -> None:
    """M0 coi FAILED là cuối đường; M1-1 mở transition FAILED->COMPENSATING/
    FAILED_FINAL (doc 02 §3.1) — chưa ai TỰ ĐỘNG đi tiếp, nhưng đường phải mở."""
    assert _VALID_TRANSITIONS[ProcessState.FAILED] == frozenset(
        {ProcessState.COMPENSATING, ProcessState.FAILED_FINAL}
    )


async def test_transition_and_event_atomic_together(
    manager: ProcessManager, store: StateStore
) -> None:
    p = await _create(manager)
    with pytest.raises(PaosError):
        # SUCCEEDED không hợp lệ trực tiếp từ CREATED -> phải rollback cả 2
        await manager.transition(p.process_id, ProcessState.SUCCEEDED)

    async def _counts(conn: aiosqlite.Connection) -> tuple[int, int]:
        c1 = await (await conn.execute("SELECT COUNT(*) FROM process_transitions")).fetchone()
        c2 = await (
            await conn.execute("SELECT COUNT(*) FROM events WHERE type LIKE 'kernel.process%'")
        ).fetchone()
        assert c1 is not None and c2 is not None
        return int(c1[0]), int(c2[0])

    transitions_count, events_count = await store.read(_counts)
    # Chỉ 1 transition (tạo CREATED) + 1 event (created) — lần SUCCEEDED lỗi không để lại vết.
    assert transitions_count == 1
    assert events_count == 1


async def _to_running(manager: ProcessManager, p: Process) -> Process:
    p = await manager.transition(p.process_id, ProcessState.PLANNING)
    p = await manager.transition(p.process_id, ProcessState.QUEUED)
    return await manager.transition(p.process_id, ProcessState.RUNNING)


@pytest.mark.parametrize(
    ("via_state", "expected_wait_event", "expected_resume_event"),
    [
        (ProcessState.WAITING, "kernel.process.waiting", "kernel.process.resumed"),
        (ProcessState.PAUSED, "kernel.process.paused", "kernel.process.resumed"),
    ],
)
async def test_waiting_and_paused_resume_back_to_running(
    manager: ProcessManager,
    store: StateStore,
    via_state: ProcessState,
    expected_wait_event: str,
    expected_resume_event: str,
) -> None:
    events = EventBus(store)
    mgr = ProcessManager(store, events)
    received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        received.append(e)

    events.subscribe("watcher", "kernel.process.*", _handler)

    p = await mgr.create(intent="test.intent", spec={}, name="x", workflow_ref="wf@1")
    p = await _to_running(mgr, p)
    p = await mgr.transition(p.process_id, via_state, reason="đợi tài nguyên")
    assert p.state is via_state

    # WAITING/PAUSED -> RUNNING là RESUME, không phải kernel.process.started lần 2.
    p = await mgr.transition(p.process_id, ProcessState.RUNNING)
    assert p.state is ProcessState.RUNNING

    event_types = [e.type for e in received]
    assert expected_wait_event in event_types
    assert expected_resume_event in event_types
    assert event_types.count("kernel.process.started") == 1  # chỉ 1 lần, lúc chạy đầu


async def test_failed_can_reach_failed_final_via_compensating(manager: ProcessManager) -> None:
    p = await _create(manager)
    p = await _to_running(manager, p)
    p = await manager.transition(
        p.process_id, ProcessState.FAILED, error_code="INTERNAL", error_message="boom"
    )
    assert p.state is ProcessState.FAILED

    p = await manager.transition(p.process_id, ProcessState.COMPENSATING)
    assert p.state is ProcessState.COMPENSATING

    p = await manager.transition(p.process_id, ProcessState.FAILED_FINAL)
    assert p.state is ProcessState.FAILED_FINAL
    assert p.ended_at is not None


async def test_get_by_pid_and_list(manager: ProcessManager) -> None:
    a = await _create(manager, name="a")
    b = await _create(manager, name="b")
    await manager.transition(b.process_id, ProcessState.PLANNING)
    await manager.transition(b.process_id, ProcessState.QUEUED)

    found = await manager.get_by_pid(a.pid)
    assert found is not None
    assert found.process_id == a.process_id

    queued = await manager.list(state=ProcessState.QUEUED)
    assert [p.process_id for p in queued] == [b.process_id]

    assert await manager.get_by_pid(999999) is None
