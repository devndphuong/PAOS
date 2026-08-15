"""Kiểm ImageAgent — đủ 6 bước, cưỡng chế manifest (doc 19 P-M3-4, doc 01 §3 UC1).

test_image_full_lifecycle_all_6_steps tích hợp THẬT: Registry + StubImageAdapter
+ StateStore/EventBus + AgentContext — không mock, cùng khuôn mẫu
tests/agents/planning/test_planning_agent.py."""

import asyncio
import json
from pathlib import Path

import aiosqlite
import pytest

from agents.image.agent import ImageAgent
from kernel.events.bus import EventBus
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub_image.adapter import StubImageAdapter
from sdk.agent import AgentContext, AgentError, AgentManifest, Artifact, ExecResult
from sdk.provider import CallContext, ErrorCode

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "agents" / "image" / "prompts"

_TEST_MANIFEST = AgentManifest(
    agent_id="image.agent",
    version=1,
    needs=["script"],
    produces=["image"],
    capabilities=["image.generate@1"],
    emits=["image.batch.created"],
)


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_call(capability_ref: str, payload: dict) -> dict:
    return {"images": [{"placeholder_text": "kết quả giả"}]}


async def _noop_emit(event_type: str, payload: dict) -> None:
    pass


def _bare_ctx(workspace_dir: Path, **overrides: object) -> AgentContext:
    kwargs: dict[str, object] = dict(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace_dir,
        agent_id="image.agent",
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
    result = await ctx.call("image.generate@1", {"prompt": "x"})
    assert result == {"images": [{"placeholder_text": "kết quả giả"}]}


async def test_call_undeclared_capability_raises_permission_denied(tmp_path: Path) -> None:
    ctx = _bare_ctx(tmp_path)
    with pytest.raises(AgentError) as exc_info:
        await ctx.call("audio.tts@1", {})
    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED


def test_manifest_loaded_from_yaml() -> None:
    assert ImageAgent.manifest.agent_id == "image.agent"
    assert ImageAgent.manifest.needs == ["script"]
    assert ImageAgent.manifest.produces == ["image"]
    assert "image.generate@1" in ImageAgent.manifest.capabilities
    assert "image.batch.created" in ImageAgent.manifest.emits


async def test_validate_rejects_empty_script() -> None:
    agent = ImageAgent()
    result = await agent.validate({"script": "  "})
    assert result.ok is False
    assert result.reason == "MISSING_SCRIPT"


async def test_review_rejects_empty_images() -> None:
    agent = ImageAgent()
    result = await agent.review(ExecResult(data={"images": []}))
    assert result.passed is False
    assert result.reason == "EMPTY_IMAGES"


async def test_resume_raises_not_checkpointable() -> None:
    agent = ImageAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.resume({})
    assert exc_info.value.code == ErrorCode.INTERNAL


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


async def test_image_full_lifecycle_all_6_steps(store: StateStore, tmp_path: Path) -> None:
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    stub = StubImageAdapter()

    async def call_capability(capability_ref: str, payload: dict) -> dict:
        cap_id, version = capability_ref.split("@")
        providers = registry.providers_for(cap_id, int(version))
        assert any(p.provider_id == "stub.image" for p in providers)
        call_ctx = CallContext(
            call_id="test",
            process_id=None,
            task_id=None,
            deadline=None,
            budget_left=None,
            privacy_class="private",
            cancel_token=asyncio.Event(),
        )
        return await stub.invoke(capability_ref, payload, call_ctx)

    events = EventBus(store)
    received = []

    async def _handler(envelope):
        received.append(envelope)

    events.subscribe("test", "image.batch.created", _handler)

    async def emit_event(event_type: str, payload: dict) -> None:
        await events.publish(event_type, source="agent.image", payload=payload)

    workspace = tmp_path / "workspace"
    ctx = AgentContext(
        process_id="proc_test",
        task_id=None,
        workspace_dir=workspace,
        agent_id="image.agent",
        prompts_dir=_PROMPTS_DIR,
        manifest=ImageAgent.manifest,
        persist_artifact=lambda a: _persist_via_store(store, a),
        call_capability=call_capability,
        emit_event=emit_event,
    )

    agent = ImageAgent()
    await agent.initialize(ctx)

    inputs = {"script": "MongoDB là cơ sở dữ liệu NoSQL."}
    validation = await agent.validate(inputs)
    assert validation.ok is True

    plan = await agent.think(inputs)
    assert "MongoDB là cơ sở dữ liệu" in plan.prompt

    exec_result = await agent.execute(plan)
    assert len(exec_result.data["images"]) == 1
    assert "[stub:image.generate" in exec_result.data["images"][0]["placeholder_text"]

    review = await agent.review(exec_result)
    assert review.passed is True

    artifacts = await agent.publish(exec_result)
    assert len(artifacts) == 1
    assert artifacts[0].type == "image"
    assert len(received) == 1
    assert received[0].payload == {"artifact_ids": [artifacts[0].artifact_id]}


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
