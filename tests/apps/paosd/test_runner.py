"""Kiểm Runner + wiring thật — đường vàng M0 (doc 13): `paosctl run` -> artifact
-> `explain` hiện trace (doc 19 P-M0-5, lát 5c). Không mock gì — StubAdapter thật,
Registry thật, StateStore thật."""

from pathlib import Path

import httpx
import pytest

from apps.paosd.wiring import Daemon, build_daemon


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

    status_resp = await client.get(f"/v1/processes/{created['pid']}")
    assert status_resp.json()["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    assert explain_resp.status_code == 200
    trace = explain_resp.json()["trace"]
    assert [e["type"] for e in trace] == [
        "kernel.process.created",
        "kernel.process.planning",
        "kernel.process.queued",
        "kernel.process.started",
        "summary.created",
        "kernel.process.completed",
    ]
    artifact_id = trace[4]["payload"]["artifact_id"]
    assert artifact_id.startswith("art_")


async def test_unknown_workflow_ref_fails_clean(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": "agent:no_such_agent@1"},
    )
    created = resp.json()

    status_resp = await client.get(f"/v1/processes/{created['pid']}")
    body = status_resp.json()
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

    status_resp = await client.get(f"/v1/processes/{created['pid']}")
    body = status_resp.json()
    assert body["state"] == "FAILED"
    assert body["error_code"] == "INVALID_INPUT"
