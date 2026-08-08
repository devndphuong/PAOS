"""Kiểm HTTP API paosd — lớp mỏng dịch HTTP <-> Kernel API (doc 04 §1, ADR-0021).

Dùng httpx.AsyncClient + ASGITransport thay vì fastapi.testclient.TestClient: TestClient
đồng bộ chạy app trong một vòng lặp asyncio riêng (qua anyio portal ở thread khác) —
khi StateStore.start() đã tạo actor task ở vòng lặp của TEST (pytest-asyncio), gọi
StateStore.write() từ vòng lặp KHÁC của TestClient khiến asyncio.Queue/Future treo
vĩnh viễn (2 vòng lặp không chia sẻ được các primitive này). AsyncClient chạy app
trong CÙNG vòng lặp với test nên không có vấn đề này.
"""

from pathlib import Path

import httpx
import pytest

from apps.paosd.app import create_app
from kernel.events.bus import EventBus
from kernel.process.manager import ProcessManager
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
def manager(store: StateStore, events: EventBus) -> ProcessManager:
    return ProcessManager(store, events)


@pytest.fixture
async def client(manager: ProcessManager, events: EventBus):
    transport = httpx.ASGITransport(app=create_app(manager, events))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_post_jobs_creates_process(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "video.create", "name": "demo", "workflow_ref": "video.from_topic@1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pid"] >= 1001
    assert "process_id" in body


async def test_get_processes_lists(client: httpx.AsyncClient) -> None:
    await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    await client.post("/v1/jobs", json={"intent": "x", "name": "b", "workflow_ref": "wf@1"})
    resp = await client.get("/v1/processes")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_get_process_by_pid(client: httpx.AsyncClient) -> None:
    created = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()
    resp = await client.get(f"/v1/processes/{created['pid']}")
    assert resp.status_code == 200
    assert resp.json()["process_id"] == created["process_id"]
    assert resp.json()["state"] == "CREATED"


async def test_get_process_by_pid_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/processes/999999")
    assert resp.status_code == 404


async def test_get_processes_filters_by_state(client: httpx.AsyncClient) -> None:
    await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    resp = await client.get("/v1/processes", params={"state": "RUNNING"})
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.get("/v1/processes", params={"state": "not_a_real_state"})
    assert resp.status_code == 400


async def test_explain_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/processes/999999/explain")
    assert resp.status_code == 404


async def test_explain_shows_created_event(client: httpx.AsyncClient) -> None:
    created = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()
    resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["process_id"] == created["process_id"]
    assert body["state"] == "CREATED"
    assert len(body["trace"]) == 1
    assert body["trace"][0]["type"] == "kernel.process.created"
    assert body["trace"][0]["process_id"] == created["process_id"]


async def test_events_not_found_for_unknown_pid(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/events", params={"pid": 999999})
    assert resp.status_code == 404


async def test_events_tail_filters_by_pid(client: httpx.AsyncClient) -> None:
    a = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()
    await client.post("/v1/jobs", json={"intent": "x", "name": "b", "workflow_ref": "wf@1"})

    resp = await client.get("/v1/events", params={"pid": a["pid"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["process_id"] == a["process_id"]


async def test_events_tail_since_seq(client: httpx.AsyncClient) -> None:
    await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    first = (await client.get("/v1/events")).json()
    assert len(first) == 1

    await client.post("/v1/jobs", json={"intent": "x", "name": "b", "workflow_ref": "wf@1"})
    resp = await client.get("/v1/events", params={"since_seq": first[0]["seq"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["seq"] > first[0]["seq"]
