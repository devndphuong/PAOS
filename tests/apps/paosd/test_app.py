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
from apps.paosd.memory_store import MemoryStore
from apps.paosd.runner import Runner
from kernel.events.bus import EventBus, EventEnvelope
from kernel.process.manager import ProcessManager
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub_embed.adapter import _embed

_REPO_ROOT = Path(__file__).resolve().parents[3]


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
def runner(manager: ProcessManager, events: EventBus, store: StateStore, tmp_path: Path) -> Runner:
    """KHÔNG subscribe vào events ở đây — file này kiểm lớp HTTP chung, không
    cần Agent chạy thật (đó là tests/apps/paosd/test_runner.py)."""
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    return Runner(manager, events, registry, store, tmp_path / "workspace")


@pytest.fixture
async def client(
    manager: ProcessManager, events: EventBus, runner: Runner, store: StateStore, tmp_path: Path
):
    app = create_app(manager, events, runner, store, tmp_path / "workspace")
    transport = httpx.ASGITransport(app=app)
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


async def test_cancel_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/processes/999999/cancel")
    assert resp.status_code == 404


async def test_cancel_created_process_succeeds(client: httpx.AsyncClient) -> None:
    # Không Runner nào subscribe events ở test này (xem docstring fixture `runner`)
    # nên process đứng yên ở CREATED — CREATED -> CANCELLED vẫn hợp lệ (doc 02 §3.1).
    created = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()
    resp = await client.post(f"/v1/processes/{created['pid']}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "CANCELLED"
    # error taxonomy hoàn chỉnh (M1-6): CANCELLED cũng phải có error_code, nhất
    # quán với FAILED — trước đó process bị hủy có error_code=NULL.
    assert body["error_code"] == "CANCELLED"


async def test_cancel_already_cancelled_is_conflict(client: httpx.AsyncClient) -> None:
    created = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()
    await client.post(f"/v1/processes/{created['pid']}/cancel")

    resp = await client.post(f"/v1/processes/{created['pid']}/cancel")
    assert resp.status_code == 400


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


async def test_dead_letters_empty_by_default(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/events/dead-letters")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_dead_letters_lists_subscriber_that_exhausted_retries(
    client: httpx.AsyncClient, events: EventBus
) -> None:
    async def _always_fails(envelope: EventEnvelope) -> None:
        raise ValueError("luôn lỗi")

    events.subscribe("broken", "kernel.process.created", _always_fails)

    await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})

    resp = await client.get("/v1/events/dead-letters")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["subscriber"] == "broken"
    assert body[0]["type"] == "kernel.process.created"
    assert "luôn lỗi" in body[0]["last_error"]


async def test_replay_unknown_subscriber_is_bad_request(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/events/replay",
        json={
            "from_ts": "0001-01-01T00:00:00",
            "to_ts": "9999-12-31T23:59:59",
            "to_subscriber": "chua_dang_ky",
        },
    )
    assert resp.status_code == 400


async def test_replay_redelivers_events_in_range(
    client: httpx.AsyncClient, events: EventBus
) -> None:
    received: list[EventEnvelope] = []

    async def _handler(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("watcher", "kernel.process.created", _handler)
    await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    assert len(received) == 1

    resp = await client.post(
        "/v1/events/replay",
        json={
            "from_ts": "0001-01-01T00:00:00",
            "to_ts": "9999-12-31T23:59:59",
            "to_subscriber": "watcher",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["replayed"] == 1
    assert len(received) == 2  # giao lại thêm 1 lần nữa


async def test_query_memory_requires_tier_or_q(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/memory")
    assert resp.status_code == 400


async def test_query_memory_lists_by_tier_without_q(
    client: httpx.AsyncClient, store: StateStore, events: EventBus
) -> None:
    ms = MemoryStore(store, events)
    await ms.write(tier="L3", content="thích video 60-90 giây", key="pref.duration")
    await ms.write(tier="L3", content="tone chuyên nghiệp", key="pref.tone")
    await ms.write(tier="L4", content="tài liệu MongoDB", key=None)

    resp = await client.get("/v1/memory", params={"tier": "L3"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(item["score"] is None for item in body)


async def test_query_memory_search_returns_ranked_vector_hits(
    client: httpx.AsyncClient, store: StateStore, events: EventBus
) -> None:
    ms = MemoryStore(store, events)
    target = "thích video 60-90 giây, tone chuyên nghiệp"
    unrelated = "quả táo đỏ ngon ngọt trong vườn"
    await ms.write(
        tier="L3", content=target, key="pref.duration", embedding=(_embed(target), "stub.embed")
    )
    await ms.write(
        tier="L3", content=unrelated, key="unrelated", embedding=(_embed(unrelated), "stub.embed")
    )

    resp = await client.get("/v1/memory", params={"tier": "L3", "q": target})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["content"] == target
    assert body[0]["matched_via"] == "vector"
    assert body[0]["score"] == pytest.approx(1.0)


async def test_query_memory_search_finds_exact_key_even_with_low_similarity_query(
    client: httpx.AsyncClient, store: StateStore, events: EventBus
) -> None:
    ms = MemoryStore(store, events)
    await ms.write(tier="L3", content="tone chuyên nghiệp", key="pref.tone")

    resp = await client.get(
        "/v1/memory",
        params={"tier": "L3", "q": "một câu bất kỳ không liên quan", "key": "pref.tone"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(item["matched_via"] == "exact_key" for item in body)
