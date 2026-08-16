"""Kiểm HTTP API paosd — lớp mỏng dịch HTTP <-> Kernel API (doc 04 §1, ADR-0021).

Dùng httpx.AsyncClient + ASGITransport thay vì fastapi.testclient.TestClient: TestClient
đồng bộ chạy app trong một vòng lặp asyncio riêng (qua anyio portal ở thread khác) —
khi StateStore.start() đã tạo actor task ở vòng lặp của TEST (pytest-asyncio), gọi
StateStore.write() từ vòng lặp KHÁC của TestClient khiến asyncio.Queue/Future treo
vĩnh viễn (2 vòng lặp không chia sẻ được các primitive này). AsyncClient chạy app
trong CÙNG vòng lặp với test nên không có vấn đề này.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from apps.paosd.app import create_app
from apps.paosd.cost_engine import CostEngine
from apps.paosd.decision_engine import DecisionEngine
from apps.paosd.memory_store import MemoryStore
from apps.paosd.plugin_manager import PluginManager
from apps.paosd.runner import Runner
from kernel import clock
from kernel.events.bus import EventBus, EventEnvelope
from kernel.permission.guard import PermissionGuard
from kernel.process.manager import ProcessManager
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from providers.stub_embed.adapter import _embed

_REPO_ROOT = Path(__file__).resolve().parents[3]

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine/TimeEngine trả None -> check() luôn
# allowed=True (BL-023, doc 19 P-M7-3). Test lớp HTTP chung này không kiểm
# Energy/Time Engine — tránh flaky theo tải CPU/NGÀY GIỜ máy thật lúc chạy.
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")
_MISSING_TIME_POLICY = Path("Z:/paos-test-time-policy-khong-ton-tai.yaml")


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
def registry(tmp_path: Path) -> Registry:
    # plugins_dir KHỚP ĐÚNG `workspace_root / "plugins"` mà `client` fixture
    # dưới truyền cho `create_app()` — PluginManager cài vào đó, Registry
    # phải quét ĐÚNG cùng thư mục mới hot-reload thấy được (P-M8-2).
    reg = Registry(
        _REPO_ROOT / "capabilities",
        _REPO_ROOT / "providers",
        plugins_dir=tmp_path / "workspace" / "plugins",
    )
    reg.load()
    return reg


@pytest.fixture
def runner(
    manager: ProcessManager, events: EventBus, registry: Registry, store: StateStore, tmp_path: Path
) -> Runner:
    """KHÔNG subscribe vào events ở đây — file này kiểm lớp HTTP chung, không
    cần Agent chạy thật (đó là tests/apps/paosd/test_runner.py)."""
    return Runner(
        manager,
        events,
        registry,
        store,
        tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )


@pytest.fixture
def decision_engine(registry: Registry, store: StateStore, events: EventBus) -> DecisionEngine:
    return DecisionEngine(registry, store, events, _REPO_ROOT / "policies" / "intents.yaml")


@pytest.fixture
def plugin_manager(registry: Registry, events: EventBus, store: StateStore, tmp_path: Path):
    guard = PermissionGuard(store, events)
    return PluginManager(registry, events, guard, tmp_path / "workspace" / "plugins")


@pytest.fixture
async def client(  # noqa: PLR0917 — mỗi tham số là 1 fixture pytest cần khai báo tường minh
    manager: ProcessManager,
    events: EventBus,
    runner: Runner,
    store: StateStore,
    tmp_path: Path,
    decision_engine: DecisionEngine,
    registry: Registry,
    plugin_manager: PluginManager,
):
    app = create_app(
        manager,
        events,
        runner,
        store,
        tmp_path / "workspace",
        decision_engine,
        registry,
        plugin_manager,
    )
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
    assert body["decisions"] == []


async def test_explain_includes_decision_records(
    client: httpx.AsyncClient, store: StateStore
) -> None:
    """P-M6-3 (doc 06 §1.1/§2.1) — ExplainResponse.decisions đọc thẳng bảng
    `decisions`, nguồn cho `paosctl explain --decisions`."""
    created = (
        await client.post("/v1/jobs", json={"intent": "x", "name": "a", "workflow_ref": "wf@1"})
    ).json()

    async def _insert(conn: Any) -> None:
        await conn.execute(
            "INSERT INTO decisions(decision_id, process_id, scope, question, candidates_json, "
            "chosen, rationale, policy_version, inputs_hash, created_at) VALUES "
            "('dec_1', ?, 'provider_selection', 'capability=text.generate@1', "
            '\'[{"id":"provider.x","eligible":true}]\', \'provider.x\', '
            "'provider.x: ưu tiên #1 theo thứ tự khai báo, khả dụng', "
            "'declared-priority@1', NULL, '2026-01-01T00:00:00+00:00')",
            (created["process_id"],),
        )

    await store.write(_insert)

    resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    body = resp.json()
    assert len(body["decisions"]) == 1
    decision = body["decisions"][0]
    assert decision["decision_id"] == "dec_1"
    assert decision["scope"] == "provider_selection"
    assert decision["chosen"] == "provider.x"
    assert decision["candidates"] == [{"id": "provider.x", "eligible": True}]


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


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


async def test_monthly_report_returns_spent_and_savings(
    client: httpx.AsyncClient, store: StateStore
) -> None:
    """doc 13 M7 exit criteria "Báo cáo tháng: đã tiêu bao nhiêu, tiết kiệm
    bao nhiêu nhờ local + cache" (doc 06 §3.4, P-M7-3)."""
    engine = CostEngine(store)
    old = clock.get_clock()
    clock.set_clock(_FixedClock(datetime(2026, 8, 10, tzinfo=UTC)))
    try:
        await engine.record_actual(
            process_id="proc_1",
            task_id=None,
            provider_id="provider.x",
            capability="text.generate@1",
            manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
            usage={"in_tokens": 20, "out_tokens": 0},
        )
        await engine.record_savings(
            process_id="proc_1",
            task_id=None,
            capability="text.generate@1",
            kind="cache",
            avoided_provider_id="provider.x",
            amount=7.0,
            currency="JPY",
        )
    finally:
        clock.set_clock(old)

    resp = await client.get("/v1/reports/monthly", params={"month": "2026-08"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["year_month"] == "2026-08"
    assert body["currency"] == "JPY"
    assert body["total_spent"] == pytest.approx(20.0)
    assert body["saved_cache"] == pytest.approx(7.0)
    assert body["saved_local"] == pytest.approx(0.0)
    assert body["total_saved"] == pytest.approx(7.0)


async def test_monthly_report_defaults_to_zero_when_no_entries(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/reports/monthly", params={"month": "2020-01"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_spent"] == 0.0
    assert body["total_saved"] == 0.0


async def test_monthly_report_rejects_malformed_month(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/reports/monthly", params={"month": "not-a-month"})
    assert resp.status_code == 400


_FAKE_PLUGIN_SRC = _REPO_ROOT / "tests" / "kernel" / "fixtures" / "plugin_process"


def _write_source_plugin(root: Path, plugin_id: str) -> Path:
    src = root / "src" / plugin_id
    src.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": "Demo Plugin",
        "version": "1.0.0",
        "paos_api": ">=1.0,<2.0",
        "provides": {
            "agents": [],
            "workflows": [],
            "capabilities": [],
            "providers": ["plugin.demo.echo@1"],
            "rubrics": [],
        },
        "permissions": {
            "fs": {"read": [], "write": []},
            "network": {"allow": []},
            "exec": [],
            "spend": {"max_per_job": 5, "currency": "JPY"},
        },
        "runtime": {
            "type": "process",
            "entry": f"{sys.executable} fake_plugin.py",
            "isolation": "subprocess",
            "limits": {},
        },
        "signature": None,
    }
    (src / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (src / "fake_plugin.py").write_text(
        (_FAKE_PLUGIN_SRC / "fake_plugin.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    provider_dir = src / "providers" / "plugin_demo_echo"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        "id: plugin.demo.echo\nimplements: [text.generate@1]\nclass: local\n"
        "privacy: private\ncost: {unit: token, in: 0, out: 0, currency: JPY}\n"
        "limits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )
    return src


async def test_plugin_inspect_shows_permissions_without_installing(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    resp = await client.get("/v1/plugins/inspect", params={"path": str(source)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["plugin_id"] == "paos.plugin.demo"
    assert body["compatible"] is True
    assert body["permissions"]["spend"]["max_per_job"] == 5

    resp_list = await client.get("/v1/plugins")
    assert resp_list.json() == []  # inspect() không cài


async def test_plugin_install_list_uninstall_round_trip(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")

    resp = await client.post("/v1/plugins", json={"path": str(source)})
    assert resp.status_code == 200
    assert resp.json()["plugin_id"] == "paos.plugin.demo"

    resp_list = await client.get("/v1/plugins")
    assert resp_list.json() == [
        {
            "plugin_id": "paos.plugin.demo",
            "version": "1.0.0",
            "status": "enabled",
            "detail": None,
        }
    ]

    resp_del = await client.delete("/v1/plugins/paos.plugin.demo")
    assert resp_del.status_code == 200
    assert (await client.get("/v1/plugins")).json() == []


async def test_plugin_disable_enable_round_trip(client: httpx.AsyncClient, tmp_path: Path) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await client.post("/v1/plugins", json={"path": str(source)})

    resp = await client.post(
        "/v1/plugins/paos.plugin.demo/disable", json={"reason": "vượt quyền fs.write"}
    )
    assert resp.status_code == 200
    body = (await client.get("/v1/plugins")).json()
    assert body[0]["status"] == "disabled"
    assert body[0]["detail"] == "vượt quyền fs.write"

    resp_enable = await client.post("/v1/plugins/paos.plugin.demo/enable")
    assert resp_enable.status_code == 200
    body_after = (await client.get("/v1/plugins")).json()
    assert body_after[0]["status"] == "enabled"


async def test_plugin_install_incompatible_paos_api_returns_400(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    manifest = yaml.safe_load((source / "plugin.yaml").read_text(encoding="utf-8"))
    manifest["paos_api"] = ">=99.0,<100.0"
    (source / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    resp = await client.post("/v1/plugins", json={"path": str(source)})
    assert resp.status_code == 400


async def test_plugin_uninstall_unknown_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.delete("/v1/plugins/khong.ton.tai")
    assert resp.status_code == 404
