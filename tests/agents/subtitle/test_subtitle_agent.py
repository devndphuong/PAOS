"""Kiểm SubtitleAgent — đủ 6 bước, cưỡng chế manifest (doc 19 P-M3-4, doc 01 §3 UC1).

Dùng provider CỤC BỘ THẬT (`providers/local_subtitle/adapter.py`, không phải
stub) — subtitle không cần AI để làm đúng (ADR-0007)."""

import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from agents.subtitle.agent import SubtitleAgent
from kernel.events.bus import EventBus
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.local_subtitle.adapter import LocalSubtitleAdapter
from sdk.agent import AgentContext, AgentError, AgentManifest, Artifact, ExecResult
from sdk.provider import CallContext, ErrorCode

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "agents" / "subtitle" / "prompts"

_TEST_MANIFEST = AgentManifest(
    agent_id="subtitle.agent",
    version=1,
    needs=["script"],
    produces=["subtitle"],
    capabilities=["text.transform@1"],
    emits=["subtitle.created"],
)


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_call(capability_ref: str, payload: dict) -> dict:
    return {"text": "1\n00:00:00,000 --> 00:00:01,000\nkết quả giả\n"}


async def _noop_emit(event_type: str, payload: dict) -> None:
    pass


def _bare_ctx(workspace_dir: Path, **overrides: object) -> AgentContext:
    kwargs: dict[str, object] = dict(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace_dir,
        agent_id="subtitle.agent",
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
    result = await ctx.call("text.transform@1", {"text": "x"})
    assert "kết quả giả" in result["text"]


async def test_call_undeclared_capability_raises_permission_denied(tmp_path: Path) -> None:
    ctx = _bare_ctx(tmp_path)
    with pytest.raises(AgentError) as exc_info:
        await ctx.call("audio.tts@1", {})
    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED


def test_manifest_loaded_from_yaml() -> None:
    assert SubtitleAgent.manifest.agent_id == "subtitle.agent"
    assert SubtitleAgent.manifest.needs == ["script"]
    assert SubtitleAgent.manifest.produces == ["subtitle"]
    assert "text.transform@1" in SubtitleAgent.manifest.capabilities
    assert "subtitle.created" in SubtitleAgent.manifest.emits


async def test_validate_rejects_empty_script() -> None:
    agent = SubtitleAgent()
    result = await agent.validate({"script": "  "})
    assert result.ok is False
    assert result.reason == "MISSING_SCRIPT"


async def test_review_rejects_empty_subtitle() -> None:
    agent = SubtitleAgent()
    result = await agent.review(ExecResult(data={"text": ""}))
    assert result.passed is False
    assert result.reason == "EMPTY_SUBTITLE"


async def test_resume_raises_not_checkpointable() -> None:
    agent = SubtitleAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.resume({})
    assert exc_info.value.code == ErrorCode.INTERNAL


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


async def test_subtitle_full_lifecycle_all_6_steps(store: StateStore, tmp_path: Path) -> None:
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    local = LocalSubtitleAdapter()

    async def call_capability(capability_ref: str, payload: dict) -> dict:
        cap_id, version = capability_ref.split("@")
        providers = registry.providers_for(cap_id, int(version))
        assert any(p.provider_id == "local.subtitle" for p in providers)
        call_ctx = CallContext(
            call_id="test",
            process_id=None,
            task_id=None,
            deadline=None,
            budget_left=None,
            privacy_class="private",
            cancel_token=asyncio.Event(),
        )
        return await local.invoke(capability_ref, payload, call_ctx)

    events = EventBus(store)
    received = []

    async def _handler(envelope):
        received.append(envelope)

    events.subscribe("test", "subtitle.created", _handler)

    async def emit_event(event_type: str, payload: dict) -> None:
        await events.publish(event_type, source="agent.subtitle", payload=payload)

    workspace = tmp_path / "workspace"
    ctx = AgentContext(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace,
        agent_id="subtitle.agent",
        prompts_dir=_PROMPTS_DIR,
        manifest=SubtitleAgent.manifest,
        persist_artifact=lambda a: _persist_via_store(store, a),
        call_capability=call_capability,
        emit_event=emit_event,
    )

    agent = SubtitleAgent()
    await agent.initialize(ctx)

    inputs = {"script": "MongoDB là cơ sở dữ liệu NoSQL. Nó lưu dữ liệu dạng JSON linh hoạt."}
    validation = await agent.validate(inputs)
    assert validation.ok is True

    plan = await agent.think(inputs)
    exec_result = await agent.execute(plan)
    assert "00:00:00" in exec_result.data["text"]

    review = await agent.review(exec_result)
    assert review.passed is True

    artifacts = await agent.publish(exec_result)
    assert len(artifacts) == 1
    assert artifacts[0].type == "subtitle"
    assert artifacts[0].mime == "text/srt"
    assert len(received) == 1
    assert received[0].payload == {"artifact_id": artifacts[0].artifact_id}


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
