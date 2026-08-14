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

from apps.paosd.wiring import Daemon, build_daemon

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


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
