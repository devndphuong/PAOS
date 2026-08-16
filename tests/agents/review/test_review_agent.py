"""Kiểm ReviewAgent — đủ 6 bước, cưỡng chế manifest (P-M4-2, doc 08 §3).
Vòng lặp tự sửa THẬT (đa agent, đa vòng) đã kiểm ở
tests/apps/paosd/test_self_correction.py — ở đây chỉ kiểm ReviewAgent ĐƠN LẺ,
1 lượt chấm, cùng khuôn mẫu tests/agents/script/test_script_agent.py."""

import json
from pathlib import Path

import pytest

from agents.review.agent import ReviewAgent
from sdk.agent import AgentContext, AgentError, AgentManifest, Artifact, ExecResult
from sdk.provider import ErrorCode
from sdk.rubric import load_rubric

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "agents" / "review" / "prompts"
_RUBRIC = load_rubric(_REPO_ROOT / "rubrics" / "script.rubric.v1.yaml")

_TEST_MANIFEST = AgentManifest(
    agent_id="review.agent",
    version=1,
    needs=["text", "rubric"],
    produces=["review"],
    capabilities=["text.generate@1"],
    emits=["quality.review.started", "quality.review.passed", "quality.review.rejected"],
)

_GOOD_SCRIPT = " ".join(f"tu{i}" for i in range(178)) + "\n\n## CTA\nHãy đăng ký kênh."


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_emit(event_type: str, payload: dict) -> None:
    pass


def _judge_call(score: float):
    async def call_capability(
        capability_ref: str,
        payload: dict,
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> tuple[dict, str | None]:
        verdict = {"score": score, "issue": None, "suggestion": None, "keep_note": None}
        return {"text": json.dumps(verdict)}, "fake.judge.provider"

    return call_capability


def _bare_ctx(workspace_dir: Path, **overrides: object) -> AgentContext:
    kwargs: dict[str, object] = dict(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace_dir,
        agent_id="review.agent",
        prompts_dir=_PROMPTS_DIR,
        manifest=_TEST_MANIFEST,
        persist_artifact=_noop_persist,
        call_capability=_judge_call(90.0),
        emit_event=_noop_emit,
    )
    kwargs.update(overrides)
    return AgentContext(**kwargs)  # type: ignore[arg-type]


def test_manifest_loaded_from_yaml() -> None:
    assert ReviewAgent.manifest.agent_id == "review.agent"
    assert ReviewAgent.manifest.capabilities == ["text.generate@1"]
    assert "quality.review.passed" in ReviewAgent.manifest.emits
    assert "quality.review.rejected" in ReviewAgent.manifest.emits
    assert ReviewAgent.manifest.checkpointable is False


async def test_validate_rejects_empty_text() -> None:
    agent = ReviewAgent()
    result = await agent.validate({"text": "   ", "rubric": _RUBRIC})
    assert result.ok is False
    assert result.reason == "MISSING_TEXT"


async def test_validate_rejects_missing_rubric() -> None:
    agent = ReviewAgent()
    result = await agent.validate({"text": "abc"})
    assert result.ok is False
    assert result.reason == "MISSING_RUBRIC"


async def test_validate_accepts_text_and_rubric() -> None:
    agent = ReviewAgent()
    result = await agent.validate({"text": "abc", "rubric": _RUBRIC})
    assert result.ok is True


async def test_execute_evaluates_rubric_and_emits_started(tmp_path: Path) -> None:
    emitted: list[tuple[str, dict]] = []

    async def _capture_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    agent = ReviewAgent()
    ctx = _bare_ctx(tmp_path, emit_event=_capture_emit, call_capability=_judge_call(90.0))
    await agent.initialize(ctx)
    plan = await agent.think({"text": _GOOD_SCRIPT, "rubric": _RUBRIC})
    exec_result = await agent.execute(plan)

    assert exec_result.data["review"]["verdict"] == "pass"
    assert emitted[0][0] == "quality.review.started"
    assert emitted[0][1]["rubric"] == "script.rubric@1"


async def test_publish_emits_passed_and_writes_review_artifact(tmp_path: Path) -> None:
    emitted: list[tuple[str, dict]] = []

    async def _capture_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    agent = ReviewAgent()
    ctx = _bare_ctx(tmp_path, emit_event=_capture_emit, call_capability=_judge_call(95.0))
    await agent.initialize(ctx)
    plan = await agent.think({"text": _GOOD_SCRIPT, "rubric": _RUBRIC})
    exec_result = await agent.execute(plan)
    review_result = await agent.review(exec_result)
    assert review_result.passed is True
    artifacts = await agent.publish(exec_result)

    assert len(artifacts) == 1
    assert artifacts[0].type == "review"
    passed_events = [e for e in emitted if e[0] == "quality.review.passed"]
    assert len(passed_events) == 1
    # 95 là điểm LLM thô cho MỌI tiêu chí llm/hybrid — điểm tổng hợp cao hơn vì
    # length/cta (deterministic) luôn 100 (xem test_self_correction._aggregate_score
    # cho công thức đầy đủ theo trọng số rubrics/script.rubric.v1.yaml).
    assert passed_events[0][1]["score"] > 95
    assert "breakdown" in passed_events[0][1]

    written = (tmp_path / "artifacts" / "proc_test" / Path(artifacts[0].path).name).read_text(
        encoding="utf-8"
    )
    assert json.loads(written)["verdict"] == "pass"


async def test_publish_emits_rejected_with_structured_feedback(tmp_path: Path) -> None:
    emitted: list[tuple[str, dict]] = []

    async def _capture_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    agent = ReviewAgent()
    ctx = _bare_ctx(tmp_path, emit_event=_capture_emit, call_capability=_judge_call(30.0))
    await agent.initialize(ctx)
    plan = await agent.think({"text": _GOOD_SCRIPT, "rubric": _RUBRIC})
    exec_result = await agent.execute(plan)
    await agent.publish(exec_result)

    rejected_events = [e for e in emitted if e[0] == "quality.review.rejected"]
    assert len(rejected_events) == 1
    payload = rejected_events[0][1]
    assert payload["score"] < 80
    assert set(payload["failed_criteria"]) >= {"logic", "hook", "tone_match"}
    assert payload["feedback"]["verdict"] == "reject"
    assert isinstance(payload["feedback"]["failed"], list)
    assert payload["feedback"]["failed"][0].keys() >= {
        "criterion",
        "score",
        "issue",
        "suggestion",
        "location",
    }


async def test_review_rejects_malformed_exec_result() -> None:
    agent = ReviewAgent()
    result = await agent.review(ExecResult(data={"review": {}}))
    assert result.passed is False
    assert result.reason == "MALFORMED_REVIEW_RESULT"


async def test_resume_raises_not_supported() -> None:
    agent = ReviewAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.resume({})
    assert exc_info.value.code == ErrorCode.INTERNAL


async def test_execute_raises_on_malformed_judge_json(tmp_path: Path) -> None:
    async def _bad_call(
        capability_ref: str,
        payload: dict,
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> tuple[dict, str | None]:
        return {"text": "not json at all"}, None

    agent = ReviewAgent()
    ctx = _bare_ctx(tmp_path, call_capability=_bad_call)
    await agent.initialize(ctx)
    plan = await agent.think({"text": _GOOD_SCRIPT, "rubric": _RUBRIC})
    with pytest.raises(AgentError) as exc_info:
        await agent.execute(plan)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT
