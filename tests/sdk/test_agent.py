"""Kiểm sdk/agent.py — AgentContext: prompt versioned, write_artifact an toàn (doc 19 P-M0-5)."""

import hashlib
import json
from pathlib import Path

import aiosqlite
import pytest

from kernel.state.db import StateStore
from sdk.agent import AgentContext, AgentError, AgentManifest, Artifact
from sdk.provider import ErrorCode

_TEST_MANIFEST = AgentManifest(
    agent_id="summarize",
    version=1,
    needs=["text"],
    produces=["summary"],
    capabilities=[],
    emits=[],
)


async def _noop_call(
    capability_ref: str, payload: dict, exclude_provider: str | None = None
) -> tuple[dict, str | None]:
    return {}, None


async def _noop_emit(event_type: str, payload: dict) -> None:
    pass


async def _noop_persist(artifact: Artifact) -> None:
    pass


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


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "v1.md").write_text("Tóm tắt văn bản sau bằng 3 câu:\n\n{{text}}", encoding="utf-8")
    return d


@pytest.fixture
def ctx(store: StateStore, workspace: Path, prompts_dir: Path) -> AgentContext:
    return AgentContext(
        process_id="proc_test",
        task_id="task_test",
        workspace_dir=workspace,
        agent_id="summarize",
        prompts_dir=prompts_dir,
        manifest=_TEST_MANIFEST,
        persist_artifact=lambda a: _persist_via_store(store, a),
        call_capability=_noop_call,
        emit_event=_noop_emit,
    )


async def test_write_artifact_computes_correct_sha256(ctx: AgentContext) -> None:
    content = "xin chào"
    artifact = await ctx.write_artifact("summary", "out.txt", content)
    assert artifact.sha256 == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert artifact.bytes == len(content.encode("utf-8"))


async def test_write_artifact_persists_row_in_db(ctx: AgentContext, store: StateStore) -> None:
    artifact = await ctx.write_artifact("summary", "out.txt", "nội dung")

    async def _count(conn: aiosqlite.Connection) -> int:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        return int(row[0])

    assert await store.read(_count) == 1


async def test_write_artifact_content_matches_file_on_disk(
    ctx: AgentContext, workspace: Path
) -> None:
    artifact = await ctx.write_artifact("summary", "out.txt", "nội dung thật")
    on_disk = (workspace / artifact.path).read_text(encoding="utf-8")
    assert on_disk == "nội dung thật"


async def test_write_artifact_rejects_path_traversal(ctx: AgentContext) -> None:
    with pytest.raises(AgentError) as exc_info:
        await ctx.write_artifact("summary", "../../../etc/passwd", "x")
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_prompt_reads_versioned_file(ctx: AgentContext) -> None:
    text = ctx.prompt("v1")
    assert "Tóm tắt văn bản" in text


def test_prompt_missing_file_raises_not_found(ctx: AgentContext) -> None:
    with pytest.raises(AgentError) as exc_info:
        ctx.prompt("v99")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_progress_rejects_out_of_range_pct(ctx: AgentContext) -> None:
    with pytest.raises(AgentError) as exc_info:
        await ctx.progress(101.0, "quá 100%")
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


async def test_progress_forwards_to_callback(workspace: Path, prompts_dir: Path) -> None:
    received: list[tuple[float, str]] = []

    async def report_progress(pct: float, message: str) -> None:
        received.append((pct, message))

    ctx = AgentContext(
        process_id="proc_test",
        task_id="task_test",
        workspace_dir=workspace,
        agent_id="summarize",
        prompts_dir=prompts_dir,
        manifest=_TEST_MANIFEST,
        persist_artifact=_noop_persist,
        call_capability=_noop_call,
        emit_event=_noop_emit,
        report_progress=report_progress,
    )
    await ctx.progress(42.0, "đang xử lý")
    assert received == [(42.0, "đang xử lý")]


async def test_checkpoint_forwards_state_to_callback(workspace: Path, prompts_dir: Path) -> None:
    received: list[dict] = []

    async def write_checkpoint(state: dict) -> int:
        received.append(state)
        return 7

    ctx = AgentContext(
        process_id="proc_test",
        task_id="task_test",
        workspace_dir=workspace,
        agent_id="summarize",
        prompts_dir=prompts_dir,
        manifest=_TEST_MANIFEST,
        persist_artifact=_noop_persist,
        call_capability=_noop_call,
        emit_event=_noop_emit,
        write_checkpoint=write_checkpoint,
    )
    seq = await ctx.checkpoint({"step": 2})
    assert seq == 7
    assert received == [{"step": 2}]


async def test_default_progress_and_checkpoint_are_noop_when_not_wired(ctx: AgentContext) -> None:
    """ctx (fixture) không truyền report_progress/write_checkpoint — vẫn phải
    gọi được mà không raise, để chỗ khởi tạo AgentContext cũ không cần sửa
    theo mỗi khi thêm callback mới (doc 04 §6: field optional = không tăng version)."""
    await ctx.progress(10.0)
    assert await ctx.checkpoint({}) == 0
