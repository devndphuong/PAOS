"""Kiểm Runner + wiring thật — đường vàng M0 (doc 13): `paosctl run` -> artifact
-> `explain` hiện trace. Từ M1-2 (doc 19), agent chạy NỀN qua worker_loop() —
`POST /v1/jobs` chỉ đảm bảo QUEUED, nên mọi test đợi qua `_wait_for_terminal()`
thay vì check ngay sau POST. Không mock gì — StubAdapter thật, Registry thật,
StateStore thật."""

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest

from apps.paosd import runner as runner_module
from apps.paosd.wiring import Daemon, build_daemon
from kernel.events.bus import EventBus
from kernel.process.manager import ProcessManager, ProcessState
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.agent import (
    AgentContext,
    AgentManifest,
    Artifact,
    ExecResult,
    Plan,
    ReviewResult,
    ValidationResult,
)

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class _CheckpointableAgent:
    """Test double cho resume() (P-M3-1) — Runner chỉ có SummarizeAgent thật,
    manifest.checkpointable=False, không tự chứng minh được đường resume()."""

    manifest = AgentManifest(
        agent_id="resume_test.agent",
        version=1,
        needs=[],
        produces=["x"],
        capabilities=[],
        emits=[],
        checkpointable=True,
    )

    def __init__(self) -> None:
        self.resume_called_with: dict[str, Any] | None = None
        self.validate_called = False
        self._ctx: AgentContext | None = None

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        self.validate_called = True
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        return Plan(prompt="")

    async def execute(self, plan: Plan) -> ExecResult:
        return ExecResult(data={"out": "from_execute"})

    async def review(self, result: ExecResult) -> ReviewResult:
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        assert self._ctx is not None
        return [await self._ctx.write_artifact("x", "out.txt", result.data["out"])]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        self.resume_called_with = checkpoint
        return ExecResult(data={"out": f"resumed:{checkpoint.get('n')}"})


@pytest.fixture
async def daemon(tmp_path: Path):
    d = await build_daemon(tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace")
    yield d
    await d.stop()


@pytest.fixture
async def client(daemon: Daemon):
    transport = httpx.ASGITransport(app=daemon.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _wait_for_terminal(
    client: httpx.AsyncClient, pid: int, max_wait_s: float = 5.0
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    body: dict[str, Any] = {}
    while loop.time() < deadline:
        body = (await client.get(f"/v1/processes/{pid}")).json()
        if body["state"] in _TERMINAL_STATES:
            return body
        await asyncio.sleep(0.01)
    return body


async def test_golden_path_run_produces_artifact_and_explain_trace(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "PAOS là một hệ điều hành AI cá nhân chạy local-first."},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    assert explain_resp.status_code == 200
    trace = explain_resp.json()["trace"]
    assert [e["type"] for e in trace] == [
        "kernel.process.created",
        "kernel.process.planning",
        "kernel.process.queued",
        "kernel.process.started",
        "kernel.process.checkpointed",
        "summary.created",
        "kernel.process.completed",
    ]
    artifact_id = trace[5]["payload"]["artifact_id"]
    assert artifact_id.startswith("art_")


async def test_post_jobs_returns_before_agent_finishes(client: httpx.AsyncClient) -> None:
    """ADR-0026: response POST /v1/jobs nghĩa là "đã tạo và đưa vào hàng đợi",
    KHÔNG còn nghĩa "đã chạy xong" — đây là điểm khác biệt cốt lõi của M1-2."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "văn bản để tóm tắt"},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    created = resp.json()
    immediate = (await client.get(f"/v1/processes/{created['pid']}")).json()
    assert immediate["state"] in {"QUEUED", "RUNNING", "SUCCEEDED"}

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"


async def test_unknown_workflow_ref_fails_clean(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": "agent:no_such_agent@1"},
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"
    assert body["error_code"] == "NOT_FOUND"


async def test_empty_text_fails_with_invalid_input(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "   "},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"
    assert body["error_code"] == "INVALID_INPUT"


async def test_error_message_is_redacted_before_persisted(
    client: httpx.AsyncClient, daemon: Daemon
) -> None:
    """workflow_ref do caller tự do đặt — chứng minh redact() thật sự chạy trước
    khi error_message ghi vào processes.error_json (doc 09 §6, SEC-01, doc 19
    P-M2-5), không chỉ đúng trên lý thuyết đọc code."""
    leaked = "sk-abcdefgh12345678"
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": f"agent:no_such_{leaked}@1"},
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"

    async def _select(conn: aiosqlite.Connection) -> str:
        cursor = await conn.execute(
            "SELECT error_json FROM processes WHERE pid = ?", (created["pid"],)
        )
        row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    error_json = await daemon.store.read(_select)
    assert leaked not in error_json
    assert "***REDACTED***" in error_json


@pytest.fixture
async def bare_runner(tmp_path: Path):
    """Runner KHÔNG subscribe on_process_created, KHÔNG chạy worker_loop() —
    khác `daemon` fixture. Test resume() cần tự tay đưa Process qua từng
    trạng thái + tự ghi checkpoint TRƯỚC khi gọi `_run_one()` một lần duy
    nhất, không muốn đua với dispatcher nền thật (doc 19 P-M3-1)."""
    store = StateStore(tmp_path / ".paos" / "state.db")
    await store.start()
    events = EventBus(store)
    registry = Registry(tmp_path / "capabilities", tmp_path / "providers")
    registry.load()
    manager = ProcessManager(store, events)
    runner = runner_module.Runner(manager, events, registry, store, tmp_path / "workspace")
    yield manager, runner
    await store.stop()


async def test_resume_called_when_running_process_has_agent_checkpoint(
    bare_runner: tuple[Any, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mô phỏng daemon restart giữa lúc RUNNING với agent checkpointable=True đã
    tự ghi checkpoint (seq > 1, qua ctx.checkpoint()) — Runner (P-M3-1) phải gọi
    resume() thay vì chạy lại validate()/think()/execute() từ đầu."""
    manager, runner = bare_runner
    agent = _CheckpointableAgent()
    monkeypatch.setitem(
        runner_module._AGENTS, "agent:resume_test.agent@1", (agent, tmp_path / "prompts")
    )

    process = await manager.create(
        intent="x", spec={}, name="r", workflow_ref="agent:resume_test.agent@1"
    )
    await manager.transition(process.process_id, ProcessState.PLANNING)
    await manager.transition(process.process_id, ProcessState.QUEUED)
    await manager.transition(process.process_id, ProcessState.RUNNING)
    await manager.write_checkpoint(process.process_id, {"phase": "running"})  # seq 1
    await manager.write_checkpoint(process.process_id, {"n": 5})  # seq 2 — agent tự ghi

    await runner._run_one(process.process_id)

    assert agent.resume_called_with == {"n": 5}
    assert agent.validate_called is False
    final = await manager.get(process.process_id)
    assert final is not None
    assert final.state == ProcessState.SUCCEEDED


async def test_resume_not_called_when_only_placeholder_checkpoint_exists(
    bare_runner: tuple[Any, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cùng kịch bản restart nhưng agent CHƯA tự ghi checkpoint nào (chỉ có mốc
    "phase: running" seq=1 do Runner ghi sẵn) — không đủ để resume(), phải chạy
    lại từ đầu (đúng hành vi M1-4 cho tới khi agent thật sự checkpoint)."""
    manager, runner = bare_runner
    agent = _CheckpointableAgent()
    monkeypatch.setitem(
        runner_module._AGENTS, "agent:resume_test.agent@1", (agent, tmp_path / "prompts")
    )

    process = await manager.create(
        intent="x", spec={}, name="r", workflow_ref="agent:resume_test.agent@1"
    )
    await manager.transition(process.process_id, ProcessState.PLANNING)
    await manager.transition(process.process_id, ProcessState.QUEUED)
    await manager.transition(process.process_id, ProcessState.RUNNING)
    await manager.write_checkpoint(process.process_id, {"phase": "running"})  # seq 1 duy nhất

    await runner._run_one(process.process_id)

    assert agent.resume_called_with is None
    assert agent.validate_called is True
    final = await manager.get(process.process_id)
    assert final is not None
    assert final.state == ProcessState.SUCCEEDED
