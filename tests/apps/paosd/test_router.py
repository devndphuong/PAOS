"""Kiểm Router — ràng buộc cứng, fallback (backoff+jitter), circuit breaker,
Decision Record (doc 06 §2.1/§2.3, ADR-0014, doc 19 P-M2-3).

Capability + provider GIẢ viết ra tmp_path (giống tests/kernel/test_registry.py)
— không dùng providers/ thật, tránh phụ thuộc Ollama/mạng thật. `_write_provider()`
CỐ Ý không ghi `adapter:` — mọi provider phải qua `preload_adapter()` mới nạp
được, nên "quên preload" chính là cách mô phỏng load thất bại thật (dùng ở
test hồi quy cho bug đã sửa lúc dựng lát này).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest
import yaml

from apps.paosd.cost_engine import CostEngine
from apps.paosd.router import Router
from kernel import clock as clock_module
from kernel.events.bus import EventBus, EventEnvelope
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.provider import CallContext, Estimate, Health, ProviderError


class _FakeAdapter:
    """`fail_times` lần gọi invoke() đầu tiên raise lỗi giả, sau đó thành công."""

    def __init__(
        self,
        *,
        fail_times: int = 0,
        error_code: str = "PROVIDER_DOWN",
        retryable: bool = True,
        estimate_cost: float = 0.0,
        usage: dict[str, int] | None = None,
    ) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.error_code = error_code
        self.retryable = retryable
        self.estimate_cost = estimate_cost
        self.usage = usage
        self.manifest = SimpleNamespace(resources=[])

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=self.estimate_cost, latency_ms=1, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(
                self.error_code, "lỗi giả lập", retryable=self.retryable, hint="test"
            )
        result: dict[str, Any] = {"text": f"ok-{self.calls}"}
        if self.usage is not None:
            result["usage"] = self.usage
        return result

    async def cancel(self, call_id: str) -> None:
        pass


class _FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _write_fixture_capability(
    caps_dir: Path, *, cacheable: bool = False, key_fields: list[str] | None = None
) -> None:
    version_dir = caps_dir / "text.generate" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "input.schema.json").write_text(
        '{"type":"object","required":["prompt"],"properties":{"prompt":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "output.schema.json").write_text(
        '{"type":"object","required":["text"],"properties":{"text":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "errors.json").write_text('["INVALID_INPUT", "PROVIDER_DOWN"]', encoding="utf-8")
    if cacheable:
        (version_dir / "cache.json").write_text(
            json.dumps({"cacheable": True, "key_fields": key_fields or ["prompt"]}),
            encoding="utf-8",
        )


def _write_provider(
    providers_dir: Path,
    provider_id: str,
    dirname: str,
    *,
    enabled: bool = True,
    privacy: str = "private",
    provider_class: str = "local",
    cost: dict[str, Any] | None = None,
) -> None:
    provider_dir = providers_dir / dirname
    provider_dir.mkdir(parents=True)
    cost_yaml = yaml.safe_dump(cost, default_flow_style=True).strip() if cost else "{}"
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [text.generate@1]\nclass: {provider_class}\n"
        f"privacy: {privacy}\ncost: {cost_yaml}\nlimits: {{}}\nresources: []\nhealth_check: {{}}\n"
        f"quality_hint: {{}}\nenabled: {'true' if enabled else 'false'}\n",
        encoding="utf-8",
    )


async def _latest_decision(store: StateStore) -> dict[str, Any]:
    async def _select(conn: aiosqlite.Connection) -> dict[str, Any]:
        # rowid, không phải created_at — fake_clock đứng yên giữa các lần gọi trong
        # cùng 1 test (vd test breaker) khiến nhiều Decision Record trùng created_at.
        cursor = await conn.execute("SELECT * FROM decisions ORDER BY rowid DESC LIMIT 1")
        row = await cursor.fetchone()
        assert row is not None, "chưa có Decision Record nào được ghi"
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row, strict=True))

    return await store.read(_select)


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
def two_provider_registry(tmp_path: Path) -> Registry:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.one", "provider_one")
    _write_provider(providers_dir, "provider.two", "provider_two")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    return reg


@pytest.fixture
def fake_clock() -> Any:
    fc = _FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    old = clock_module.get_clock()
    clock_module.set_clock(fc)
    yield fc
    clock_module.set_clock(old)


async def test_call_succeeds_and_records_decision(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id = manifests[0].provider_id
    adapter = _FakeAdapter()
    two_provider_registry.preload_adapter(first_id, adapter)
    # provider còn lại CỐ Ý không preload — không được thử vì cái đầu đã thành công.

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    result, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert result == {"text": "ok-1"}
    decision = await _latest_decision(store)
    assert decision["chosen"] == first_id
    assert decision["scope"] == "provider_selection"
    assert "ưu tiên #1" in decision["rationale"]


async def _seed_provider_quality(
    store: StateStore, provider_id: str, task_class: str, quality_ewma: float, quality_n: int
) -> None:
    async def _insert(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO provider_stats(provider_id, task_class, quality_ewma, quality_n, "
            "success_count, fail_count, updated_at) VALUES (?, ?, ?, ?, 0, 0, ?)",
            (provider_id, task_class, quality_ewma, quality_n, "2026-01-01T00:00:00+00:00"),
        )

    await store.write(_insert)


async def test_ranks_by_quality_ewma_when_enough_samples(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M6-2 (doc 06 §2.2) — provider #2 (theo thứ tự khai báo) có quality_ewma
    cao hơn VÀ đủ n>=5 -> Router chọn #2 dù #1 đứng trước trong Registry."""
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id
    two_provider_registry.preload_adapter(first_id, _FakeAdapter())
    two_provider_registry.preload_adapter(second_id, _FakeAdapter())
    await _seed_provider_quality(store, first_id, "unknown", 0.5, 5)
    await _seed_provider_quality(store, second_id, "unknown", 0.9, 5)

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    _, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert chosen == second_id
    decision = await _latest_decision(store)
    assert decision["chosen"] == second_id
    assert "quality_ewma cao nhất" in decision["rationale"]


async def test_ranking_ignored_when_fewer_than_5_samples(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """Cold start / dữ liệu chưa đủ (n<5, doc 06 §2.2) -> KHÔNG xếp lại, giữ
    nguyên thứ tự khai báo — 0 rủi ro hồi quy cho hành vi trước P-M6-2."""
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id
    two_provider_registry.preload_adapter(first_id, _FakeAdapter())
    two_provider_registry.preload_adapter(second_id, _FakeAdapter())
    await _seed_provider_quality(store, second_id, "unknown", 0.95, 4)  # n=4 < 5

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    _, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert chosen == first_id
    decision = await _latest_decision(store)
    assert "ưu tiên #1 theo thứ tự khai báo" in decision["rationale"]


def _write_routing_policy(path: Path, profiles: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, **profiles}), encoding="utf-8")


async def test_privacy_fit_favors_local_under_private_profile(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M6-3 (doc 06 §2.2) — 2 provider CÙNG quality_ewma (n>=5) nhưng khác
    class: local vs cloud. Profile "private" (w_p cao) phải ưu tiên local nhờ
    số hạng P, dù Q̂ hòa tuyệt đối."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.local", "p_local", provider_class="local")
    _write_provider(providers_dir, "provider.cloud", "p_cloud", provider_class="cloud")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.local", _FakeAdapter())
    reg.preload_adapter("provider.cloud", _FakeAdapter())
    await _seed_provider_quality(store, "provider.local", "unknown", 0.6, 5)
    await _seed_provider_quality(store, "provider.cloud", "unknown", 0.6, 5)

    routing_path = tmp_path / "policies" / "routing.yaml"
    _write_routing_policy(
        routing_path,
        {
            "default": {"w_q": 0.40, "w_c": 0.35, "w_l": 0.15, "w_p": 0.05, "w_r": 0.05},
            "profiles": {"private": {"w_q": 0.10, "w_c": 0.0, "w_l": 0.0, "w_p": 0.90, "w_r": 0.0}},
        },
    )
    router = Router(reg, events, store, {}, tmp_path, routing_policy_path=routing_path)
    _, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1", profile="private")

    assert chosen == "provider.local"


async def test_success_rate_breaks_tie_via_r_term(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """R (success_rate) tham gia điểm khi CÓ lịch sử thử (không cần ngưỡng n,
    khác Q̂) — 2 provider cùng quality_ewma nhưng khác success_rate."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.reliable", "p_a")
    _write_provider(providers_dir, "provider.flaky", "p_b")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.reliable", _FakeAdapter())
    reg.preload_adapter("provider.flaky", _FakeAdapter())
    await _seed_provider_quality(store, "provider.reliable", "unknown", 0.6, 5)
    await _seed_provider_quality(store, "provider.flaky", "unknown", 0.6, 5)

    async def _seed_attempts(provider_id: str, success: int, fail: int) -> None:
        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE provider_stats SET success_count = ?, fail_count = ? "
                "WHERE provider_id = ? AND task_class = 'unknown'",
                (success, fail, provider_id),
            )

        await store.write(_insert)

    await _seed_attempts("provider.reliable", 9, 1)
    await _seed_attempts("provider.flaky", 1, 9)

    router = Router(reg, events, store, {}, tmp_path)
    _, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert chosen == "provider.reliable"


async def test_routing_policy_hot_reload_changes_ranking_without_restart(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """`routing.yaml` đọc lại MỖI LẦN gọi — sửa file giữa 2 lượt gọi phải đổi
    ngay kết quả xếp hạng, không cần dựng lại Router (P-M6-3, doc 06 §2.2)."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.local", "p_local", provider_class="local")
    _write_provider(providers_dir, "provider.cloud", "p_cloud", provider_class="cloud")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.local", _FakeAdapter())
    reg.preload_adapter("provider.cloud", _FakeAdapter())
    await _seed_provider_quality(store, "provider.local", "unknown", 0.5, 5)
    await _seed_provider_quality(store, "provider.cloud", "unknown", 0.9, 5)

    routing_path = tmp_path / "policies" / "routing.yaml"
    _write_routing_policy(
        routing_path, {"default": {"w_q": 1.0, "w_c": 0.0, "w_l": 0.0, "w_p": 0.0, "w_r": 0.0}}
    )
    router = Router(reg, events, store, {}, tmp_path, routing_policy_path=routing_path)

    _, first_chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert first_chosen == "provider.cloud"  # w_q thắng tuyệt đối -> quality_ewma cao hơn

    # Sửa file NGAY GIỮA 2 lượt gọi — không dựng lại Router.
    _write_routing_policy(
        routing_path, {"default": {"w_q": 0.0, "w_c": 0.0, "w_l": 0.0, "w_p": 1.0, "w_r": 0.0}}
    )
    _, second_chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_2")
    assert second_chosen == "provider.local"  # w_p thắng tuyệt đối -> local có P cao hơn


def _write_budget_policy(path: Path, budgets: dict[str, dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "budgets": budgets}), encoding="utf-8")


async def test_budget_ask_blocks_call_entirely(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M7-1 (doc 06 §3.2 mốc 2) — exit criteria: vượt ngân sách per_job
    (on_exceed=ask) phải CHẶN NGAY, không thử candidate khác, không âm thầm
    tiêu tiền (P12)."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.pricey", "p_a")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.pricey", _FakeAdapter(estimate_cost=999.0))

    budget_path = tmp_path / "policies" / "budget.yaml"
    _write_budget_policy(
        budget_path, {"per_job": {"max": 20, "currency": "JPY", "on_exceed": "ask"}}
    )
    router = Router(reg, events, store, {}, tmp_path, budget_policy_path=budget_path)

    with pytest.raises(ProviderError) as exc_info:
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert exc_info.value.code.value == "BUDGET_EXCEEDED"
    assert exc_info.value.context["approval_id"]  # PermissionGuard CONFIRM (P-M7-1 phần 2)
    decision = await _latest_decision(store)
    assert decision["chosen"] is None
    candidates = json.loads(decision["candidates_json"])
    assert candidates[0]["reason"] == "BUDGET_PER_JOB_ASK"


async def test_budget_ask_goes_through_permission_guard_audit_trail(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M7-1 phần 2 — "chặn VÀ hỏi": budget ask phải đi qua PermissionGuard
    thật (audit_log ghi CONFIRM + phát permission.approval.requested), không
    chỉ raise lỗi suông."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.pricey", "p_a")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.pricey", _FakeAdapter(estimate_cost=999.0))

    received: list[EventEnvelope] = []

    async def _capture(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("test", "permission.approval.requested", _capture)

    budget_path = tmp_path / "policies" / "budget.yaml"
    _write_budget_policy(
        budget_path, {"per_job": {"max": 20, "currency": "JPY", "on_exceed": "ask"}}
    )
    router = Router(reg, events, store, {}, tmp_path, budget_policy_path=budget_path)

    with pytest.raises(ProviderError):
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert len(received) == 1
    assert received[0].payload["action"] == "cost.budget_exceeded"
    assert received[0].payload["target"] == "provider.pricey"

    async def _audit_rows(conn: aiosqlite.Connection) -> list[Any]:
        cursor = await conn.execute("SELECT action, tier, target FROM audit_log ORDER BY id")
        return await cursor.fetchall()

    rows = await store.read(_audit_rows)
    assert any(r[0] == "cost.budget_exceeded" and r[1] == "CONFIRM" for r in rows)


async def test_profile_auto_upgrades_to_economy_when_day_budget_warn_exceeded(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M7-1 phần 2 (doc 06 §6) — chi tiêu hôm nay vượt warn_at_pct -> Router
    tự chọn profile "economy" (w_c cao) thay vì "default", KHI caller không tự
    truyền profile khác."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.local", "p_local", provider_class="local")
    _write_provider(providers_dir, "provider.cloud", "p_cloud", provider_class="cloud")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.local", _FakeAdapter())
    reg.preload_adapter("provider.cloud", _FakeAdapter())
    # đủ n>=5 cho cả 2, cloud nhỉnh hơn 1 chút -> "default"/"quality" chọn cloud,
    # "economy" (w_c cao, w_q thấp) phải đảo sang local.
    await _seed_provider_quality(store, "provider.local", "unknown", 0.60, 5)
    await _seed_provider_quality(store, "provider.cloud", "unknown", 0.65, 5)

    budget_path = tmp_path / "policies" / "budget.yaml"
    budget_path.parent.mkdir(parents=True, exist_ok=True)
    budget_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "budgets": {"per_day": {"max": 100, "currency": "JPY", "on_exceed": "ask"}},
                "warn_at_pct": 80,
            }
        ),
        encoding="utf-8",
    )
    cost_engine = CostEngine(store, budget_path)
    await cost_engine.record_actual(
        process_id="proc_prev",
        task_id=None,
        provider_id="provider.cloud",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
        usage={"in_tokens": 85, "out_tokens": 0},  # 85% > warn_at_pct=80
    )

    routing_path = tmp_path / "policies" / "routing.yaml"
    _write_routing_policy(
        routing_path,
        {
            "default": {"w_q": 0.90, "w_c": 0.0, "w_l": 0.0, "w_p": 0.10, "w_r": 0.0},
            "profiles": {"economy": {"w_q": 0.05, "w_c": 0.0, "w_l": 0.0, "w_p": 0.95, "w_r": 0.0}},
        },
    )
    router = Router(
        reg,
        events,
        store,
        {},
        tmp_path,
        routing_policy_path=routing_path,
        budget_policy_path=budget_path,
    )

    _, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert chosen == "provider.local"  # economy (w_p cao) thắng, không phải default (w_q cao)


async def test_budget_force_local_skips_cloud_falls_back_to_local(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """`force_local`/`block_cloud` loại candidate CLOUD, thử tiếp — KHÔNG chặn
    hẳn (khác `ask`) — đúng "vượt → BUDGET_EXCEEDED, thử fallback local"."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.cloud", "p_cloud", provider_class="cloud")
    _write_provider(providers_dir, "provider.local", "p_local", provider_class="local")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.cloud", _FakeAdapter(estimate_cost=999.0))
    reg.preload_adapter("provider.local", _FakeAdapter(estimate_cost=0.0))

    budget_path = tmp_path / "policies" / "budget.yaml"
    _write_budget_policy(
        budget_path, {"per_job": {"max": 20, "currency": "JPY", "on_exceed": "force_local"}}
    )
    router = Router(reg, events, store, {}, tmp_path, budget_policy_path=budget_path)

    result, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert chosen == "provider.local"
    assert result == {"text": "ok-1"}


async def test_budget_local_candidate_not_blocked_by_force_local(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """Candidate ĐÃ local không bị chặn bởi force_local/block_cloud dù estimate
    cao — mục đích của 2 hành động này là đẩy về local, chặn candidate local là
    vô nghĩa."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.local", "p_local", provider_class="local")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.local", _FakeAdapter(estimate_cost=999.0))

    budget_path = tmp_path / "policies" / "budget.yaml"
    _write_budget_policy(
        budget_path, {"per_job": {"max": 20, "currency": "JPY", "on_exceed": "force_local"}}
    )
    router = Router(reg, events, store, {}, tmp_path, budget_policy_path=budget_path)

    result, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert chosen == "provider.local"
    assert result == {"text": "ok-1"}


async def test_successful_call_records_actual_cost(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """doc 06 §3.2 mốc 3 — sau invoke() thành công, ghi cost_entries THẬT từ
    usage trả về, khớp provider.yaml::cost."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(
        providers_dir,
        "provider.metered",
        "p_a",
        cost={"unit": "token", "in": 0.001, "out": 0.002, "currency": "JPY"},
    )
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter(
        "provider.metered", _FakeAdapter(usage={"in_tokens": 100, "out_tokens": 50})
    )

    router = Router(reg, events, store, {}, tmp_path)
    await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    async def _select(conn: aiosqlite.Connection) -> tuple[Any, ...] | None:
        cursor = await conn.execute(
            "SELECT provider_id, capability, amount, currency, estimated FROM cost_entries "
            "WHERE process_id = ?",
            ("proc_1",),
        )
        return await cursor.fetchone()

    row = await store.read(_select)
    assert row is not None
    assert row[0] == "provider.metered"
    assert row[1] == "text.generate@1"
    assert row[2] == pytest.approx(100 * 0.001 + 50 * 0.002)
    assert row[3] == "JPY"
    assert row[4] == 0


async def test_exclude_provider_routes_to_next_candidate(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """P-M4-2 (ADR-0008, RSK-10) — exclude_provider loại candidate đó ra khỏi
    ứng viên, KHÔNG cần nó lỗi/breaker mở, để Review Agent tránh dùng cùng
    provider đã sinh artifact đang chấm."""
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id
    two_provider_registry.preload_adapter(first_id, _FakeAdapter())
    two_provider_registry.preload_adapter(second_id, _FakeAdapter())

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    _, chosen = await router.call(
        "text.generate@1", {"prompt": "x"}, "proc_1", exclude_provider=first_id
    )

    assert chosen == second_id
    decision = await _latest_decision(store)
    candidates = json.loads(decision["candidates_json"])
    excluded = next(c for c in candidates if c["id"] == first_id)
    assert excluded["eligible"] is False
    assert excluded["reason"] == "EXCLUDED_GENERATOR"


async def test_exclude_provider_leaving_zero_candidates_raises(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.solo", "provider_solo")
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    reg.preload_adapter("provider.solo", _FakeAdapter())

    router = Router(reg, events, store, {}, tmp_path)
    with pytest.raises(ProviderError):
        await router.call(
            "text.generate@1", {"prompt": "x"}, "proc_1", exclude_provider="provider.solo"
        )


async def test_fallback_to_second_provider_on_retryable_error(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id

    failing = _FakeAdapter(fail_times=999)  # luôn lỗi PROVIDER_DOWN (retryable mặc định)
    succeeding = _FakeAdapter()
    two_provider_registry.preload_adapter(first_id, failing)
    two_provider_registry.preload_adapter(second_id, succeeding)

    published: list[EventEnvelope] = []

    async def _on_fallback(envelope: EventEnvelope) -> None:
        published.append(envelope)

    events.subscribe("test", "capability.fallback.triggered", _on_fallback)

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    result, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert result == {"text": "ok-1"}
    assert failing.calls == 1
    assert succeeding.calls == 1
    assert len(published) == 1
    assert published[0].payload["failed_provider_id"] == first_id
    assert published[0].payload["next_provider_id"] == second_id
    assert published[0].payload["error_code"] == "PROVIDER_DOWN"

    decision = await _latest_decision(store)
    assert decision["chosen"] == second_id
    assert first_id in decision["rationale"]


async def test_non_retryable_error_stops_without_trying_next(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id

    failing = _FakeAdapter(fail_times=999, error_code="INVALID_INPUT", retryable=False)
    never_called = _FakeAdapter()
    two_provider_registry.preload_adapter(first_id, failing)
    two_provider_registry.preload_adapter(second_id, never_called)

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    with pytest.raises(ProviderError) as exc_info:
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert exc_info.value.code == "INVALID_INPUT"
    assert failing.calls == 1
    assert never_called.calls == 0  # doc 06 §2.3: lỗi input/logic, thử tiếp không giúp gì


async def test_load_failure_falls_through_to_next_candidate(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    """Hồi quy: bug thật phát hiện lúc dựng lát này — load thất bại (thiếu
    `adapter:`) từng bị hiểu nhầm là 'dừng hẳn chuỗi fallback' (dùng chung field
    `retryable=False`) thay vì 'bỏ qua candidate này, thử tiếp'."""
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id, second_id = manifests[0].provider_id, manifests[1].provider_id

    succeeding = _FakeAdapter()
    two_provider_registry.preload_adapter(second_id, succeeding)
    # first_id CỐ Ý không preload -> Registry.load_adapter() raise thật (thiếu adapter:).

    router = Router(two_provider_registry, events, store, {}, tmp_path)
    result, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert result == {"text": "ok-1"}
    assert succeeding.calls == 1
    decision = await _latest_decision(store)
    assert decision["chosen"] == second_id
    candidates = json.loads(decision["candidates_json"])
    failed = next(c for c in candidates if c["id"] == first_id)
    assert failed["eligible"] is False
    assert failed["reason"].startswith("LOAD_FAILED")


async def test_disabled_provider_is_excluded_as_candidate(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.disabled", "disabled_one", enabled=False)
    _write_provider(providers_dir, "provider.ok", "ok_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    ok_adapter = _FakeAdapter()
    reg.preload_adapter("provider.ok", ok_adapter)

    router = Router(reg, events, store, {}, tmp_path)
    result, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert result == {"text": "ok-1"}
    assert ok_adapter.calls == 1
    decision = await _latest_decision(store)
    candidates = json.loads(decision["candidates_json"])
    disabled = next(c for c in candidates if c["id"] == "provider.disabled")
    assert disabled["eligible"] is False
    assert disabled["reason"] == "DISABLED"


async def test_privacy_mismatch_provider_is_excluded(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.cloud", "cloud_one", privacy="shared")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    router = Router(reg, events, store, {}, tmp_path)
    with pytest.raises(ProviderError) as exc_info:
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert exc_info.value.code == "PROVIDER_DOWN"
    decision = await _latest_decision(store)
    assert decision["chosen"] is None
    candidates = json.loads(decision["candidates_json"])
    assert candidates[0]["reason"] == "PRIVACY_MISMATCH"


async def test_writes_decision_record_even_when_all_candidates_fail(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.solo", "solo_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    failing = _FakeAdapter(fail_times=999)
    reg.preload_adapter("provider.solo", failing)

    router = Router(reg, events, store, {}, tmp_path)
    with pytest.raises(ProviderError) as exc_info:
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert exc_info.value.code == "PROVIDER_DOWN"
    decision = await _latest_decision(store)
    assert decision["chosen"] is None
    assert "Hết ứng viên" in decision["rationale"]


async def test_no_provider_registered_raises_not_found(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    _write_fixture_capability(caps_dir)
    reg = Registry(caps_dir, tmp_path / "providers")
    reg.load()

    router = Router(reg, events, store, {}, tmp_path)
    with pytest.raises(ProviderError) as exc_info:
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert exc_info.value.code == "NOT_FOUND"


async def test_breaker_opens_after_3_failures_then_recovers(
    tmp_path: Path, events: EventBus, store: StateStore, fake_clock: _FakeClock
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "provider.solo", "solo_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    adapter = _FakeAdapter(fail_times=999)
    reg.preload_adapter("provider.solo", adapter)
    router = Router(reg, events, store, {}, tmp_path)

    for _ in range(3):
        with pytest.raises(ProviderError):
            await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert adapter.calls == 3

    # Lần thứ 4 trong 60s: breaker OPEN, candidate bị loại TRƯỚC khi thử invoke().
    with pytest.raises(ProviderError):
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert adapter.calls == 3
    decision = await _latest_decision(store)
    candidates = json.loads(decision["candidates_json"])
    assert candidates[0]["reason"] == "BREAKER_OPEN"

    # Qua mốc 60s: HALF_OPEN — cho thử lại 1 lần, lần này cho thành công.
    fake_clock.advance(61)
    adapter.fail_times = 0
    result, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert result == {"text": "ok-4"}
    assert adapter.calls == 4


async def test_cache_hit_skips_provider_and_records_cache_hit_decision(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir, cacheable=True)
    _write_provider(providers_dir, "provider.solo", "solo_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    adapter = _FakeAdapter()
    reg.preload_adapter("provider.solo", adapter)
    router = Router(reg, events, store, {}, tmp_path)

    first, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    second, _ = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert first == second == {"text": "ok-1"}
    assert adapter.calls == 1  # lần 2 KHÔNG gọi adapter — trúng cache

    decision = await _latest_decision(store)
    assert decision["scope"] == "cache_hit"
    assert decision["chosen"] == "provider.solo"
    assert decision["candidates_json"] is None
    assert decision["inputs_hash"] is not None


async def test_different_payload_is_not_a_cache_hit(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir, cacheable=True)
    _write_provider(providers_dir, "provider.solo", "solo_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    adapter = _FakeAdapter()
    reg.preload_adapter("provider.solo", adapter)
    router = Router(reg, events, store, {}, tmp_path)

    await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    await router.call("text.generate@1", {"prompt": "y"}, "proc_1")

    assert adapter.calls == 2  # payload khác -> cache_key khác -> không trúng


async def test_non_cacheable_capability_never_caches(
    two_provider_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    manifests = two_provider_registry.providers_for("text.generate", 1)
    first_id = manifests[0].provider_id
    adapter = _FakeAdapter()
    two_provider_registry.preload_adapter(first_id, adapter)
    router = Router(two_provider_registry, events, store, {}, tmp_path)

    await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    # _write_fixture_capability() mặc định cacheable=False (không cache.json) —
    # cùng payload gọi 2 lần vẫn phải chạy thật cả 2 lần.
    assert adapter.calls == 2


async def test_decision_rationale_and_candidates_json_are_redacted(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    """Chứng minh redact() thật sự được gọi trước khi ghi Decision Record (doc 09
    §6, SEC-01, doc 19 P-M2-5) — không phải provider_id thật sự là secret (không
    bao giờ nên như vậy), chỉ dựng kịch bản tổng hợp để khoá lại đường ghi này."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)
    _write_provider(providers_dir, "sk-abcdefgh12345678", "leaky_one")
    reg = Registry(caps_dir, providers_dir)
    reg.load()

    adapter = _FakeAdapter()
    reg.preload_adapter("sk-abcdefgh12345678", adapter)
    router = Router(reg, events, store, {}, tmp_path)

    await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    decision = await _latest_decision(store)
    assert "sk-abcdefgh12345678" not in str(decision["rationale"])
    assert "sk-abcdefgh12345678" not in str(decision["candidates_json"])
