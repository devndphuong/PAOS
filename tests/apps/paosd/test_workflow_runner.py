"""Kiểm apps/paosd/workflow_runner.py — thực thi DAG: điều kiện, parallel,
retry, vòng lặp có giới hạn, compensation (doc 04 §4, ADR-0006, doc 19 P-M3-2).
Capability + provider GIẢ viết ra tmp_path (giống tests/apps/paosd/test_router.py)
— không phụ thuộc Ollama/mạng thật."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apps.paosd.router import Router
from apps.paosd.workflow_runner import StepExecutionFailed, WorkflowRunner
from kernel.events.bus import EventBus, EventEnvelope
from kernel.process.manager import ProcessManager
from kernel.process.tasks import TaskStore
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from kernel.workflow.spec import parse_workflow_spec
from sdk.agent import (
    AgentContext,
    AgentManifest,
    Artifact,
    ExecResult,
    Plan,
    ReviewResult,
    ValidationResult,
)
from sdk.provider import CallContext, Estimate, Health, ProviderError


class _FakeAdapter:
    """`fail_times` lần gọi invoke() đầu tiên raise lỗi giả, sau đó thành công
    (cùng pattern tests/apps/paosd/test_router.py::_FakeAdapter)."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.manifest = SimpleNamespace(resources=[])

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError("PROVIDER_DOWN", "lỗi giả lập", retryable=True, hint="test")
        return {"text": f"ok-{self.calls}-{payload.get('prompt', '')}"}

    async def cancel(self, call_id: str) -> None:
        pass


class _EchoAgent:
    """Agent tối giản cho test — không gọi capability nào, chỉ phản chiếu input
    thành output, không ghi artifact (publish() trả [] hợp lệ theo Protocol)."""

    manifest = AgentManifest(
        agent_id="echo.agent", version=1, needs=[], produces=["echo"], capabilities=[], emits=[]
    )

    def __init__(self) -> None:
        self.think_calls: list[dict[str, Any]] = []

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        self.think_calls.append(inputs)
        return Plan(prompt="")

    async def execute(self, plan: Plan) -> ExecResult:
        return ExecResult(data={"echoed": self.think_calls[-1].get("value")})

    async def review(self, result: ExecResult) -> ReviewResult:
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        return []

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise NotImplementedError


def _write_fixture_capability(caps_dir: Path, capability_id: str = "text.generate") -> None:
    version_dir = caps_dir / capability_id / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "input.schema.json").write_text(
        '{"type":"object","required":["prompt"],"properties":{"prompt":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "output.schema.json").write_text(
        '{"type":"object","required":["text"],"properties":{"text":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "errors.json").write_text('["INVALID_INPUT", "PROVIDER_DOWN"]', encoding="utf-8")


def _write_provider(
    providers_dir: Path, dirname: str, provider_id: str, capability_id: str = "text.generate"
) -> None:
    provider_dir = providers_dir / dirname
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [{capability_id}@1]\nclass: local\nprivacy: private\n"
        "cost: {}\nlimits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )


@dataclass
class Harness:
    """Gộp mọi collaborator của WorkflowRunner vào 1 fixture — tránh test nào
    cũng cần >6 tham số riêng lẻ (PLR0917, max-args=6 ở pyproject.toml)."""

    manager: ProcessManager
    events: EventBus
    registry: Registry
    store: StateStore
    router: Router
    tmp_path: Path
    echo_agent: _EchoAgent

    def runner(self) -> WorkflowRunner:
        self.registry.preload_agent("echo.agent", 1, self.echo_agent, self.tmp_path)
        return WorkflowRunner(
            self.manager,
            self.events,
            self.registry,
            self.store,
            self.router,
            self.tmp_path / "workspace",
        )

    async def new_process(self, workflow_ref: str = "workflow:test@1") -> str:
        p = await self.manager.create(intent="x", spec={}, name="x", workflow_ref=workflow_ref)
        return p.process_id


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
def registry(tmp_path: Path) -> Registry:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir, "text.generate")
    _write_fixture_capability(caps_dir, "text.review")
    _write_provider(providers_dir, "fake_provider", "fake.provider", "text.generate")
    _write_provider(providers_dir, "fake_review_provider", "fake.review.provider", "text.review")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    return reg


@pytest.fixture
def fake_adapter() -> _FakeAdapter:
    return _FakeAdapter()


@pytest.fixture
def fake_review_adapter() -> _FakeAdapter:
    return _FakeAdapter()


@pytest.fixture
def harness(
    store: StateStore,
    events: EventBus,
    registry: Registry,
    tmp_path: Path,
    fake_adapter: _FakeAdapter,
    fake_review_adapter: _FakeAdapter,
) -> Harness:
    registry.preload_adapter("fake.provider", fake_adapter)
    registry.preload_adapter("fake.review.provider", fake_review_adapter)
    manager = ProcessManager(store, events)
    router = Router(registry, events, store, {}, tmp_path / "workspace")
    return Harness(manager, events, registry, store, router, tmp_path, _EchoAgent())


async def test_capability_then_agent_step_flows_data_through_dag(harness: Harness) -> None:
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
          - {id: echo, kind: agent, ref: echo.agent@1, with: {value: "${steps.draft.output.text}"}}
        """
    )
    process_id = await harness.new_process()
    context = await harness.runner().run(process_id, spec, {})
    assert context["steps"]["draft"]["output"] == {"text": "ok-1-hello"}
    assert context["steps"]["echo"]["output"]["echoed"] == "ok-1-hello"


async def test_when_false_skips_step_and_emits_event(harness: Harness) -> None:
    received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        received.append(e)

    harness.events.subscribe("watcher", "workflow.step.skipped", _handler)
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        inputs: {flag: {type: bool}}
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
          - id: maybe
            kind: capability
            ref: text.generate@1
            when: "${inputs.flag == true}"
            with: {prompt: "${steps.draft.output.text}"}
        """
    )
    process_id = await harness.new_process()
    context = await harness.runner().run(process_id, spec, {"flag": False})
    assert context["steps"]["maybe"] == {"output": None, "skipped": True}
    assert len(received) == 1
    assert received[0].payload["step_id"] == "maybe"


async def test_retry_recovers_from_transient_failure(
    harness: Harness, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.fail_times = 1
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - id: draft
            kind: capability
            ref: text.generate@1
            with: {prompt: hello}
            retry: {max: 2}
        """
    )
    process_id = await harness.new_process()
    context = await harness.runner().run(process_id, spec, {})
    assert context["steps"]["draft"]["output"]["text"] == "ok-2-hello"
    assert fake_adapter.calls == 2


async def test_retry_exhausted_raises_step_execution_failed(
    harness: Harness, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.fail_times = 5
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - id: draft
            kind: capability
            ref: text.generate@1
            with: {prompt: hello}
            retry: {max: 1}
        """
    )
    process_id = await harness.new_process()
    with pytest.raises(StepExecutionFailed) as exc_info:
        await harness.runner().run(process_id, spec, {})
    assert exc_info.value.step_id == "draft"
    assert fake_adapter.calls == 2  # 1 lần đầu + 1 retry, đúng retry.max


async def test_parallel_runs_sub_steps_and_merges_into_group_namespace(harness: Harness) -> None:
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - id: media
            kind: parallel
            steps:
              - {id: a, kind: capability, ref: text.generate@1, with: {prompt: a}}
              - {id: b, kind: capability, ref: text.generate@1, with: {prompt: b}}
          - id: after
            kind: capability
            ref: text.generate@1
            with: {prompt: "${steps.media.a.output.text}"}
        """
    )
    process_id = await harness.new_process()
    context = await harness.runner().run(process_id, spec, {})
    assert set(context["steps"]["media"].keys()) == {"a", "b"}
    assert context["steps"]["media"]["a"]["output"]["text"].startswith("ok-")
    assert context["steps"]["after"]["output"]["text"] is not None


async def test_parallel_group_records_honest_time_saved_vs_sequential(
    harness: Harness,
) -> None:
    """P-M3-4: group 'parallel' cũng là 1 Task, sự kiện `kernel.task.completed`
    của nó mang thêm `parallel: {wall_ms, sequential_ms, saved_ms}` — kiểm
    `sequential_ms` KHỚP ĐÚNG tổng thời gian đo thật của từng sub-task (không
    phải hằng số bịa ra), đúng yêu cầu "đo và ghi vào explain, đừng bịa"
    (doc 19 P-M3-4)."""
    events_received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        events_received.append(e)

    harness.events.subscribe("watcher", "kernel.task.completed", _handler)

    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - id: media
            kind: parallel
            steps:
              - {id: a, kind: capability, ref: text.generate@1, with: {prompt: a}}
              - {id: b, kind: capability, ref: text.generate@1, with: {prompt: b}}
        """
    )
    process_id = await harness.new_process()
    await harness.runner().run(process_id, spec, {})

    group_events = [e for e in events_received if e.payload["step_id"] == "media"]
    assert len(group_events) == 1
    parallel = group_events[0].payload["parallel"]
    assert parallel["wall_ms"] >= 0
    assert parallel["sequential_ms"] >= 0
    assert parallel["saved_ms"] == max(0, parallel["sequential_ms"] - parallel["wall_ms"])

    tasks = TaskStore(harness.store, harness.events)
    a_task = await tasks.get_by_step(process_id, "a")
    b_task = await tasks.get_by_step(process_id, "b")
    assert a_task is not None and a_task.started_at and a_task.ended_at
    assert b_task is not None and b_task.started_at and b_task.ended_at

    def _duration_ms(start_iso: str, end_iso: str) -> int:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        return int((end - start).total_seconds() * 1000)

    expected_sequential = _duration_ms(a_task.started_at, a_task.ended_at) + _duration_ms(
        b_task.started_at, b_task.ended_at
    )
    assert parallel["sequential_ms"] == expected_sequential


async def test_on_fail_loop_reruns_target_step_then_succeeds(
    harness: Harness, fake_review_adapter: _FakeAdapter
) -> None:
    """draft dùng text.generate@1 (luôn thành công); review dùng text.review@1
    RIÊNG (fail_times=1) để kiểm soát độc lập — cùng 1 capability cho cả 2 step
    sẽ không phân biệt được lỗi thuộc step nào vì adapter không biết step_id."""
    fake_review_adapter.fail_times = 1

    events_received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        events_received.append(e)

    harness.events.subscribe("watcher", "workflow.loop.entered", _handler)

    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
          - id: review
            kind: capability
            ref: text.review@1
            with: {prompt: "${steps.draft.output.text}"}
            on_fail: {goto: draft, max_loops: 2}
        """
    )
    process_id = await harness.new_process()
    await harness.runner().run(process_id, spec, {})

    assert len(events_received) == 1
    assert events_received[0].payload == {"step_id": "draft", "loop_count": 1}
    # draft chạy 2 lần (ban đầu + sau loop-back) -> vẫn 1 Task duy nhất, attempts tăng
    tasks = TaskStore(harness.store, harness.events)
    draft_task = await tasks.get_by_step(process_id, "draft")
    assert draft_task is not None
    assert draft_task.attempts == 1


async def test_on_fail_loop_exhausted_raises(
    harness: Harness, fake_review_adapter: _FakeAdapter
) -> None:
    fake_review_adapter.fail_times = 999  # không bao giờ thành công trong ngân sách test
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
          - id: review
            kind: capability
            ref: text.review@1
            with: {prompt: "${steps.draft.output.text}"}
            on_fail: {goto: draft, max_loops: 2}
        """
    )
    process_id = await harness.new_process()
    with pytest.raises(StepExecutionFailed) as exc_info:
        await harness.runner().run(process_id, spec, {})
    assert exc_info.value.step_id == "review"


async def test_compensate_runs_named_steps_best_effort(
    harness: Harness, fake_adapter: _FakeAdapter
) -> None:
    fake_adapter.fail_times = 999
    runner = harness.runner()
    spec = parse_workflow_spec(
        """
        id: t
        version: 1
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
          - id: cleanup
            kind: capability
            ref: text.generate@1
            with: {prompt: cleanup}
        on_error: {compensate: [cleanup], notify: true}
        """
    )
    process_id = await harness.new_process()
    with pytest.raises(StepExecutionFailed) as exc_info:
        await runner.run(process_id, spec, {})

    events_received: list[EventEnvelope] = []

    async def _handler(e: EventEnvelope) -> None:
        events_received.append(e)

    harness.events.subscribe("watcher", "workflow.compensation.started", _handler)
    fake_adapter.fail_times = 0  # cleanup phải thành công được
    await runner.compensate(process_id, spec, exc_info.value.context)
    assert fake_adapter.calls >= 2  # đã gọi cho draft (thất bại) + cleanup (thành công)


async def test_freeze_writes_workflow_json_into_project_dir(harness: Harness) -> None:
    spec = parse_workflow_spec(
        """
        id: demo.pipeline
        version: 1
        steps:
          - {id: draft, kind: capability, ref: text.generate@1, with: {prompt: hello}}
        """
    )
    process_id = await harness.new_process()
    await harness.runner().run(process_id, spec, {})

    frozen_path = harness.tmp_path / "workspace" / "projects" / process_id / "workflow.json"
    assert frozen_path.is_file()
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    assert frozen["id"] == "demo.pipeline"
    assert frozen["resolved_order"] == ["draft"]
