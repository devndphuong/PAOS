"""Test ĐỐI KHÁNG cho Privacy Filter (doc 07 §6, doc 09 §7, P-M5-4) — doc 13 M5
exit criterion "Memory L3 không bao giờ rời máy khi `privacy: private`".

Thiết kế theo đúng câu hỏi doc 19 P-M5-4: "bạn sẽ cố tình tấn công hệ thống
của chính mình bằng bao nhiêu đường khác nhau?" — mỗi test dưới đây là MỘT
đường tấn công riêng:

1. Provider `class: cloud` tự khai `privacy: private` (GIAN DỐI/cấu hình sai)
   — kiểm Router không tin tưởng self-declaration, chỉ tin CLASS cấu trúc.
2. `invoke()` của adapter cloud có thực sự KHÔNG BAO GIỜ được gọi hay không
   (không chỉ "kết quả không dùng" — dữ liệu không được PHÁT ĐI, đếm calls).
3. Event `privacy.cloud_send.blocked` — có bị rò rỉ nội dung payload thật
   (chính thứ đang được bảo vệ) vào chính event ghi lại việc chặn không?
4. Decision Record (candidates_json/rationale, đi vào Trace/`paosctl explain`)
   — có rò rỉ nội dung payload thật không?
5. Thông điệp lỗi khi TOÀN BỘ candidate bị chặn — có rò rỉ payload thật
   trong `message`/`context`/`hint` không?
6. Cache — 1 lượt gọi bị chặn có vô tình để lại dấu vết trong cache (mà một
   lượt gọi sau, không bị chặn, có thể vô tình "trúng" phải) không?
7. Mặc định `contains_private_l3=False` — chứng minh cờ THẬT SỰ opt-in
   (không tình cờ chặn luôn mọi cloud provider, kể cả payload không mang L3).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from apps.paosd.router import Router
from kernel.events.bus import EventBus, EventEnvelope
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.provider import CallContext, Estimate, Health, ProviderError

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine.check() luôn allowed=True (P-M7-2).
# Tránh flaky theo tải CPU MÁY THẬT lúc chạy (default Router trỏ
# policies/energy.yaml THẬT trong repo).
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")


def _make_router(*args: Any, **kwargs: Any) -> Router:
    kwargs.setdefault("energy_policy_path", _MISSING_ENERGY_POLICY)
    return Router(*args, **kwargs)


_SECRET = "so_thich_rieng_tu_khong_ai_duoc_biet_98765"  # noqa: S105 - khong phai secret that, chi la du lieu gia dung de kiem tra ro ri


class _CountingAdapter:
    """Đếm số lần `invoke()` THẬT SỰ được gọi — bằng chứng trực tiếp payload
    có bị PHÁT ĐI hay không, không chỉ suy luận từ kết quả trả về."""

    def __init__(self) -> None:
        self.calls = 0
        self.received_payloads: list[dict[str, Any]] = []
        self.manifest = SimpleNamespace(resources=[])

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self.calls += 1
        self.received_payloads.append(payload)
        return {"text": "khong duoc phep toi day"}

    async def cancel(self, call_id: str) -> None:
        pass


def _write_fixture_capability(caps_dir: Path, *, cacheable: bool = False) -> None:
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
            json.dumps({"cacheable": True, "key_fields": ["prompt"]}), encoding="utf-8"
        )


def _write_provider(
    providers_dir: Path,
    provider_id: str,
    dirname: str,
    *,
    provider_class: str = "local",
    privacy: str = "private",
) -> None:
    provider_dir = providers_dir / dirname
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [text.generate@1]\nclass: {provider_class}\n"
        f"privacy: {privacy}\ncost: {{}}\nlimits: {{}}\nresources: []\nhealth_check: {{}}\n"
        f"quality_hint: {{}}\n",
        encoding="utf-8",
    )


async def _latest_decision(store: StateStore) -> dict[str, Any]:
    async def _select(conn: aiosqlite.Connection) -> Any:
        cursor = await conn.execute("SELECT * FROM decisions ORDER BY rowid DESC LIMIT 1")
        row = await cursor.fetchone()
        assert row is not None
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


def _make_registry(
    tmp_path: Path, *, cacheable: bool = False, extra_providers: list[dict[str, Any]] | None = None
) -> tuple[Registry, Path]:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir, cacheable=cacheable)
    for i, spec in enumerate(extra_providers or []):
        _write_provider(
            providers_dir, spec["id"], f"p{i}", **{k: v for k, v in spec.items() if k != "id"}
        )
    reg = Registry(caps_dir, providers_dir)
    reg.load()
    return reg, providers_dir


# --- 1 & 2: provider cloud tự khai "privacy: private" (gian dối) vẫn bị chặn --


async def test_lying_cloud_provider_declaring_privacy_private_is_still_blocked(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    """Đường tấn công cốt lõi: provider CLASS cloud nhưng TỰ KHAI
    `privacy: private` (đúng hoặc gian dối/cấu hình sai) — kiểm tra bằng
    chứng KHÔNG dựa vào self-declaration mà bị Privacy Filter chặn tuyệt đối
    khi payload mang L3, và adapter.invoke() KHÔNG BAO GIỜ được gọi (dữ liệu
    không hề rời máy)."""
    reg, _providers_dir = _make_registry(
        tmp_path,
        extra_providers=[
            {"id": "provider.evil_cloud", "provider_class": "cloud", "privacy": "private"}
        ],
    )
    adapter = _CountingAdapter()
    reg.preload_adapter("provider.evil_cloud", adapter)
    router = _make_router(reg, events, store, {}, tmp_path)

    with pytest.raises(ProviderError):
        await router.call(
            "text.generate@1",
            {"prompt": f"sở thích của tôi: {_SECRET}"},
            "proc_1",
            contains_private_l3=True,
        )

    assert adapter.calls == 0  # KHÔNG BAO GIỜ gọi invoke() — dữ liệu không rời máy
    assert adapter.received_payloads == []
    decision = await _latest_decision(store)
    candidates = json.loads(decision["candidates_json"])
    assert candidates[0]["reason"] == "PRIVACY_L3_BLOCKED"


async def test_cloud_fallback_skipped_local_used_instead(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    """Cloud là ứng viên ưu tiên #1, local là #2 — với contains_private_l3=True,
    Router phải bỏ qua cloud HOÀN TOÀN và dùng local, không thử cloud trước."""
    reg, _ = _make_registry(
        tmp_path,
        extra_providers=[
            {"id": "provider.cloud", "provider_class": "cloud", "privacy": "shared"},
            {"id": "provider.local", "provider_class": "local", "privacy": "private"},
        ],
    )
    cloud_adapter = _CountingAdapter()
    local_adapter = _CountingAdapter()
    reg.preload_adapter("provider.cloud", cloud_adapter)
    reg.preload_adapter("provider.local", local_adapter)
    router = _make_router(reg, events, store, {}, tmp_path)

    _result, chosen = await router.call(
        "text.generate@1", {"prompt": _SECRET}, "proc_1", contains_private_l3=True
    )

    assert chosen == "provider.local"
    assert cloud_adapter.calls == 0
    assert local_adapter.calls == 1


# --- 3: event chặn không được rò rỉ nội dung payload thật ------------------


async def test_privacy_blocked_event_does_not_leak_payload_content(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    reg, _ = _make_registry(
        tmp_path, extra_providers=[{"id": "provider.evil_cloud", "provider_class": "cloud"}]
    )
    reg.preload_adapter("provider.evil_cloud", _CountingAdapter())
    router = _make_router(reg, events, store, {}, tmp_path)

    received: list[EventEnvelope] = []

    async def _handler(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("watcher", "privacy.cloud_send.blocked", _handler)

    with pytest.raises(ProviderError):
        await router.call(
            "text.generate@1", {"prompt": _SECRET}, "proc_1", contains_private_l3=True
        )

    assert len(received) == 1
    envelope = received[0]
    assert set(envelope.payload.keys()) == {"capability", "provider_id", "decision_id"}
    assert _SECRET not in json.dumps(envelope.payload, ensure_ascii=False)
    assert envelope.payload["provider_id"] == "provider.evil_cloud"
    assert envelope.payload["capability"] == "text.generate@1"


# --- 4: Decision Record (Trace) không được rò rỉ nội dung payload thật -----


async def test_decision_record_does_not_leak_payload_content_when_blocked(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    reg, _ = _make_registry(
        tmp_path, extra_providers=[{"id": "provider.evil_cloud", "provider_class": "cloud"}]
    )
    reg.preload_adapter("provider.evil_cloud", _CountingAdapter())
    router = _make_router(reg, events, store, {}, tmp_path)

    with pytest.raises(ProviderError):
        await router.call(
            "text.generate@1", {"prompt": _SECRET}, "proc_1", contains_private_l3=True
        )

    decision = await _latest_decision(store)
    assert _SECRET not in str(decision["candidates_json"])
    assert _SECRET not in str(decision["rationale"])
    assert _SECRET not in json.dumps(decision, ensure_ascii=False, default=str)


# --- 5: thông điệp lỗi không rò rỉ nội dung payload thật --------------------


async def test_error_raised_when_all_candidates_blocked_has_no_payload_leak(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    reg, _ = _make_registry(
        tmp_path, extra_providers=[{"id": "provider.solo_cloud", "provider_class": "cloud"}]
    )
    reg.preload_adapter("provider.solo_cloud", _CountingAdapter())
    router = _make_router(reg, events, store, {}, tmp_path)

    with pytest.raises(ProviderError) as exc_info:
        await router.call(
            "text.generate@1", {"prompt": _SECRET}, "proc_1", contains_private_l3=True
        )

    err = exc_info.value
    assert _SECRET not in err.message
    assert _SECRET not in json.dumps(err.context, ensure_ascii=False)
    assert _SECRET not in err.hint


# --- 6: 1 lượt gọi bị chặn không để lại dấu vết trong cache -----------------


async def test_blocked_attempt_does_not_populate_cache(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    """Capability cacheable — lượt gọi contains_private_l3=True bị chặn hoàn
    toàn KHÔNG được tạo cache entry nào cho candidate cloud (candidate cloud
    còn chưa từng vào `eligible`, nên `_lookup_cache`/cache write không bao
    giờ chạm tới nó) — lượt gọi SAU (không mang cờ) với payload GIỐNG HỆT vẫn
    phải gọi adapter thật, không được "trúng cache" từ lượt bị chặn."""
    reg, _ = _make_registry(
        tmp_path,
        cacheable=True,
        extra_providers=[{"id": "provider.cloud", "provider_class": "cloud"}],
    )
    adapter = _CountingAdapter()
    reg.preload_adapter("provider.cloud", adapter)
    router = _make_router(reg, events, store, {}, tmp_path)

    with pytest.raises(ProviderError):
        await router.call("text.generate@1", {"prompt": "x"}, "proc_1", contains_private_l3=True)
    assert adapter.calls == 0

    # Lượt gọi SAU, KHÔNG mang cờ, cùng payload — phải gọi adapter thật (không
    # trúng cache từ lượt trước, vì lượt trước chưa từng ghi gì vào cache).
    __result, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")
    assert chosen == "provider.cloud"
    assert adapter.calls == 1


# --- 7: mặc định False KHÔNG chặn — chứng minh cờ thật sự opt-in -----------


async def test_default_contains_private_l3_false_does_not_block_cloud(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    """Regression bảo vệ: Privacy Filter KHÔNG được vô tình chặn MỌI cloud
    provider bất kể payload gì — chỉ chặn khi caller CHỦ ĐỘNG khai
    contains_private_l3=True. 8 Agent thật hôm nay không truyền cờ này (giá
    trị mặc định False) và không được ảnh hưởng gì bởi lát cắt này."""
    reg, _ = _make_registry(
        tmp_path, extra_providers=[{"id": "provider.cloud", "provider_class": "cloud"}]
    )
    adapter = _CountingAdapter()
    reg.preload_adapter("provider.cloud", adapter)
    router = _make_router(reg, events, store, {}, tmp_path)

    __result, chosen = await router.call("text.generate@1", {"prompt": "x"}, "proc_1")

    assert chosen == "provider.cloud"
    assert adapter.calls == 1
