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

from apps.paosd.router import Router
from kernel import clock as clock_module
from kernel.events.bus import EventBus, EventEnvelope
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.provider import CallContext, Estimate, Health, ProviderError


class _FakeAdapter:
    """`fail_times` lần gọi invoke() đầu tiên raise lỗi giả, sau đó thành công."""

    def __init__(
        self, *, fail_times: int = 0, error_code: str = "PROVIDER_DOWN", retryable: bool = True
    ) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.error_code = error_code
        self.retryable = retryable
        self.manifest = SimpleNamespace(resources=[])

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderError(
                self.error_code, "lỗi giả lập", retryable=self.retryable, hint="test"
            )
        return {"text": f"ok-{self.calls}"}

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
) -> None:
    provider_dir = providers_dir / dirname
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [text.generate@1]\nclass: local\nprivacy: {privacy}\n"
        f"cost: {{}}\nlimits: {{}}\nresources: []\nhealth_check: {{}}\nquality_hint: {{}}\n"
        f"enabled: {'true' if enabled else 'false'}\n",
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
    result = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert result == {"text": "ok-1"}
    decision = await _latest_decision(store)
    assert decision["chosen"] == first_id
    assert decision["scope"] == "provider_selection"
    assert "ưu tiên #1" in decision["rationale"]


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
    result = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

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
    result = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

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
    result = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

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
    result = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
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

    first = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    second = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

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
