"""Kiểm RenderAgent — đủ 6 bước, cưỡng chế manifest, images TÙY CHỌN (doc 19
P-M3-5, doc 13 M3 exit criteria LOC-05). Caller thật đầu tiên của
ctx.progress() (có từ P-M3-1) — kiểm luôn progress() được gọi đúng thứ tự."""

import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from agents.render.agent import RenderAgent
from kernel.events.bus import EventBus
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub_render.adapter import StubRenderAdapter
from sdk.agent import AgentContext, AgentError, AgentManifest, Artifact, ExecResult
from sdk.provider import CallContext, ErrorCode

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "agents" / "render" / "prompts"

_TEST_MANIFEST = AgentManifest(
    agent_id="render.agent",
    version=1,
    needs=["image", "voice", "subtitle"],
    produces=["video"],
    capabilities=["video.render@1"],
    emits=["video.rendered"],
)


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_call(
    capability_ref: str,
    payload: dict,
    exclude_provider: str | None = None,
    contains_private_l3: bool = False,
) -> tuple[dict, str | None]:
    return {
        "video": {"placeholder_text": "kết quả giả", "duration_sec": 5.0, "image_count": 1}
    }, None


async def _noop_emit(event_type: str, payload: dict) -> None:
    pass


def _bare_ctx(workspace_dir: Path, **overrides: object) -> AgentContext:
    kwargs: dict[str, object] = dict(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace_dir,
        agent_id="render.agent",
        prompts_dir=_PROMPTS_DIR,
        manifest=_TEST_MANIFEST,
        persist_artifact=_noop_persist,
        call_capability=_noop_call,
        emit_event=_noop_emit,
    )
    kwargs.update(overrides)
    return AgentContext(**kwargs)  # type: ignore[arg-type]


async def test_call_allowed_capability_succeeds(tmp_path: Path) -> None:
    ctx = _bare_ctx(tmp_path)
    result = await ctx.call("video.render@1", {"prompt": "x"})
    assert result["video"]["duration_sec"] == 5.0


async def test_call_undeclared_capability_raises_permission_denied(tmp_path: Path) -> None:
    ctx = _bare_ctx(tmp_path)
    with pytest.raises(AgentError) as exc_info:
        await ctx.call("audio.tts@1", {})
    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED


def test_manifest_loaded_from_yaml() -> None:
    assert RenderAgent.manifest.agent_id == "render.agent"
    assert RenderAgent.manifest.needs == ["image", "voice", "subtitle"]
    assert RenderAgent.manifest.produces == ["video"]
    assert "video.render@1" in RenderAgent.manifest.capabilities
    assert "video.rendered" in RenderAgent.manifest.emits


async def test_validate_rejects_missing_voice() -> None:
    agent = RenderAgent()
    result = await agent.validate({"subtitle": "abc"})
    assert result.ok is False
    assert result.reason == "MISSING_VOICE"


async def test_validate_rejects_missing_subtitle() -> None:
    agent = RenderAgent()
    result = await agent.validate({"voice": {"duration_sec": 5.0}, "subtitle": ""})
    assert result.ok is False
    assert result.reason == "MISSING_SUBTITLE"


async def test_validate_accepts_missing_images_degraded_mode() -> None:
    """LOC-05 — images KHÔNG bắt buộc, đúng ý đồ degraded mode."""
    agent = RenderAgent()
    result = await agent.validate({"voice": {"duration_sec": 5.0}, "subtitle": "abc"})
    assert result.ok is True


async def test_review_rejects_empty_video() -> None:
    agent = RenderAgent()
    result = await agent.review(ExecResult(data={"video": {}}))
    assert result.passed is False
    assert result.reason == "EMPTY_VIDEO"


async def test_resume_raises_not_checkpointable() -> None:
    agent = RenderAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.resume({})
    assert exc_info.value.code == ErrorCode.INTERNAL


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


async def test_render_full_lifecycle_reports_progress_and_handles_degraded(
    store: StateStore, tmp_path: Path
) -> None:
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    stub = StubRenderAdapter()

    async def call_capability(
        capability_ref: str,
        payload: dict,
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> tuple[dict, str | None]:
        cap_id, version = capability_ref.split("@")
        providers = registry.providers_for(cap_id, int(version))
        assert any(p.provider_id == "stub.render" for p in providers)
        call_ctx = CallContext(
            call_id="test",
            process_id=None,
            task_id=None,
            deadline=None,
            budget_left=None,
            privacy_class="private",
            cancel_token=asyncio.Event(),
        )
        return await stub.invoke(capability_ref, payload, call_ctx), None

    events = EventBus(store)
    rendered_events = []
    progress_calls: list[tuple[float, str]] = []

    async def _handler(envelope):
        rendered_events.append(envelope)

    events.subscribe("test", "video.rendered", _handler)

    async def emit_event(event_type: str, payload: dict) -> None:
        await events.publish(event_type, source="agent.render", payload=payload)

    async def report_progress(pct: float, message: str) -> None:
        progress_calls.append((pct, message))

    workspace = tmp_path / "workspace"
    ctx = AgentContext(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace,
        agent_id="render.agent",
        prompts_dir=_PROMPTS_DIR,
        manifest=RenderAgent.manifest,
        persist_artifact=lambda a: _persist_via_store(store, a),
        call_capability=call_capability,
        emit_event=emit_event,
        report_progress=report_progress,
    )

    agent = RenderAgent()
    await agent.initialize(ctx)

    # KHÔNG có "images" — mô phỏng chế độ degraded (LOC-05).
    inputs = {"voice": {"placeholder_text": "voice", "duration_sec": 12.4}, "subtitle": "phụ đề"}
    validation = await agent.validate(inputs)
    assert validation.ok is True

    plan = await agent.think(inputs)
    exec_result = await agent.execute(plan)
    assert exec_result.data["video"]["duration_sec"] == 12.4
    assert exec_result.data["video"]["image_count"] == 0

    review = await agent.review(exec_result)
    assert review.passed is True

    artifacts = await agent.publish(exec_result)
    assert len(artifacts) == 1
    assert artifacts[0].type == "video"
    assert len(rendered_events) == 1
    assert rendered_events[0].payload["degraded"] is True

    # progress() gọi đúng thứ tự tăng dần, kết thúc ở 100%.
    assert [pct for pct, _ in progress_calls] == [20.0, 90.0, 100.0]


async def _persist_via_store(store: StateStore, artifact: Artifact) -> None:
    async def _insert(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
            "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                artifact.artifact_id,
                artifact.process_id,
                artifact.task_id,
                artifact.type,
                artifact.path,
                artifact.mime,
                artifact.sha256,
                artifact.bytes,
                json.dumps(artifact.produced_by),
                artifact.supersedes,
                artifact.created_at,
            ),
        )

    await store.write(_insert)
