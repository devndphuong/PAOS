"""Kiểm kernel/process/tasks.py — TaskStore (doc 03 §2.3/§3, P-M3-2)."""

from pathlib import Path

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.process.manager import ProcessManager
from kernel.process.tasks import TaskState, TaskStore
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
def tasks(store: StateStore, events: EventBus) -> TaskStore:
    return TaskStore(store, events)


@pytest.fixture
async def process_id(store: StateStore, events: EventBus) -> str:
    manager = ProcessManager(store, events)
    p = await manager.create(intent="x", spec={}, name="x", workflow_ref="workflow:w@1")
    return p.process_id


async def test_schedule_creates_pending_task_and_emits_event(
    tasks: TaskStore, events: EventBus, process_id: str
) -> None:
    received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        received.append(e)

    events.subscribe("watcher", "kernel.task.scheduled", _handler)

    task = await tasks.schedule(
        process_id=process_id,
        step_id="detect",
        kind="capability",
        ref="doc.parse@1",
        depends_on=[],
        inputs={"file": "a.pdf"},
    )
    assert task.state is TaskState.PENDING
    assert task.step_id == "detect"
    assert task.attempts == 0
    assert len(received) == 1
    assert received[0].payload == {"task_id": task.task_id, "step_id": "detect", "attempt": 0}


async def test_start_transitions_to_running_and_sets_started_at(
    tasks: TaskStore, process_id: str
) -> None:
    task = await tasks.schedule(
        process_id=process_id, step_id="a", kind="capability", ref="x@1", depends_on=[], inputs={}
    )
    started = await tasks.start(task.task_id)
    assert started.state is TaskState.RUNNING
    assert started.started_at is not None


async def test_succeed_transitions_to_succeeded_with_quality_score(
    tasks: TaskStore, process_id: str
) -> None:
    task = await tasks.schedule(
        process_id=process_id, step_id="a", kind="agent", ref="x@1", depends_on=[], inputs={}
    )
    await tasks.start(task.task_id)
    done = await tasks.succeed(task.task_id, quality_score=91.0)
    assert done.state is TaskState.SUCCEEDED
    assert done.ended_at is not None
    assert done.quality_score == 91.0


async def test_fail_transitions_to_failed_with_error_code(
    tasks: TaskStore, process_id: str
) -> None:
    task = await tasks.schedule(
        process_id=process_id, step_id="a", kind="capability", ref="x@1", depends_on=[], inputs={}
    )
    await tasks.start(task.task_id)
    failed = await tasks.fail(task.task_id, error_code="PROVIDER_DOWN")
    assert failed.state is TaskState.FAILED
    assert failed.error_code == "PROVIDER_DOWN"


async def test_retry_keeps_same_task_id_increments_attempts(
    tasks: TaskStore, process_id: str
) -> None:
    task = await tasks.schedule(
        process_id=process_id, step_id="a", kind="capability", ref="x@1", depends_on=[], inputs={}
    )
    await tasks.start(task.task_id)
    await tasks.fail(task.task_id, error_code="PROVIDER_TIMEOUT")

    retried = await tasks.retry(task.task_id)
    assert retried.task_id == task.task_id
    assert retried.attempts == 1
    assert retried.state is TaskState.PENDING
    assert retried.error_code is None


async def test_list_for_process_returns_in_order(tasks: TaskStore, process_id: str) -> None:
    a = await tasks.schedule(
        process_id=process_id, step_id="a", kind="capability", ref="x@1", depends_on=[], inputs={}
    )
    b = await tasks.schedule(
        process_id=process_id, step_id="b", kind="agent", ref="y@1", depends_on=["a"], inputs={}
    )
    listed = await tasks.list_for_process(process_id)
    assert [t.task_id for t in listed] == sorted([a.task_id, b.task_id])
    assert listed[[t.task_id for t in listed].index(b.task_id)].depends_on == ["a"]


async def test_get_unknown_task_returns_none(tasks: TaskStore) -> None:
    assert await tasks.get("task_khong_ton_tai") is None


async def test_start_unknown_task_raises_not_found(tasks: TaskStore) -> None:
    with pytest.raises(PaosError) as exc_info:
        await tasks.start("task_khong_ton_tai")
    assert exc_info.value.code == ErrorCode.NOT_FOUND
