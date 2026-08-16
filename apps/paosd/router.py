"""Router — chọn provider cho 1 lượt gọi capability: ràng buộc cứng, chuỗi
fallback (backoff+jitter), circuit breaker, Decision Record (doc 06 §2.1/§2.3,
ADR-0014, doc 19 P-M2-3).

Ở apps/, KHÔNG ở kernel/: cần bắt sdk.provider.ProviderError, mà Kernel bị
cấm import sdk/ (MNT-06). Đây là nơi thay cho vòng lặp "chỉ thử provider ĐẦU
TIÊN nạp được" cũ ở Runner._make_call_capability() (P-M2-1) — giờ fallback
THẬT theo kết quả invoke(), không chỉ theo load().

CHƯA làm ở M2 (doc 19 P-M2-3): công thức chấm điểm + provider ranking — chọn
theo ĐÚNG thứ tự Registry.providers_for() trả về (ưu tiên khai báo, chưa đảm
bảo ổn định). P-M6-2/P-M6-3 đã lấp 3/5 số hạng: `_rank_candidates()` xếp lại
theo `w_q·Q̂ + w_p·P + w_r·R` (renormalize trên số hạng THẬT SỰ có — Q̂ cần
`quality_n>=5`, R cần ≥1 lần thử, P luôn tính được từ `provider.yaml::class`)
khi ≥1 ứng viên đủ Q̂; cold start vẫn giữ nguyên thứ tự khai báo. Trọng số đọc
từ `policies/routing.yaml` theo `profile` (default/economy/quality/private,
đọc lại MỖI LẦN gọi — hot reload, không cache). CHƯA áp Ĉ/L̂ (cần Cost Engine
M7 + latency tracking) và CHƯA có rule engine tự đổi profile theo budget (đó
cũng cần Cost Engine) — xem docstring `_rank_candidates()`.

doc 06 §2.1 liệt kê 9 ràng buộc cứng — chỉ 3 làm được với dữ liệu ĐÃ CÓ hôm
nay (enabled, breaker OPEN, privacy_class). P-M7-1/P-M7-2 lấp thêm "budget" và
"thiếu resource" (CPU/pin/nhiệt thật, ADR-0030 — xem `_check_energy()`). Còn
lại: health FAIL, offline_only, context/size — cần Time Engine, health poller,
hoặc tokenizer, chưa tồn tại. HOÃN có ghi chú, không giả vờ làm bằng giá trị
hardcode luôn True/luôn qua.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import yaml

from apps.paosd.cache_store import CacheHit, CacheStore
from apps.paosd.cost_engine import CostEngine
from apps.paosd.energy_engine import EnergyEngine
from apps.paosd.provider_stats import ProviderStats, normalize_task_class
from kernel import clock, ids
from kernel.errors import PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.permission.guard import PermissionGuard
from kernel.redact import redact
from kernel.registry.registry import CapabilitySpec, ProviderManifest, Registry
from kernel.state.db import StateStore
from sdk.provider import CallContext, ProviderError

# Cùng công thức với kernel/events/bus.py (M1-5a) — nhất quán, không bịa hằng số mới.
_BACKOFF_BASE_S = 0.05
_BACKOFF_JITTER_S = 0.03

_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_OPEN_S = 60.0

# Cùng tiền lệ EventBus._DEFAULT_SCHEMA_DIR (kernel/events/bus.py) — default trỏ
# file THẬT trong repo, để 26+ chỗ gọi Router(...) đã có (test + production)
# không cần sửa khi thêm tham số này (doc 19 P-M6-3).
_DEFAULT_ROUTING_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "routing.yaml"
# Cùng lý do — default trỏ policies/budget.yaml thật (doc 19 P-M7-1).
_DEFAULT_BUDGET_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "budget.yaml"
# Cùng lý do — default trỏ policies/energy.yaml thật (doc 19 P-M7-2).
_DEFAULT_ENERGY_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "energy.yaml"
_DEFAULT_PROFILE = "default"
_LOCAL_PRIVACY_FIT = 1.0
_NON_LOCAL_PRIVACY_FIT = 0.4


@dataclass(frozen=True)
class _RoutingWeights:
    w_q: float
    w_c: float
    w_l: float
    w_p: float
    w_r: float


def _load_routing_policy(path: Path) -> dict[str, _RoutingWeights]:
    """Đọc lại MỖI LẦN gọi — hot reload thật, cùng tiền lệ
    `DecisionEngine._load_intents()` (P-M6-1): sửa `policies/routing.yaml` có
    hiệu lực ngay lần gọi kế tiếp, không cache, không restart daemon. Thiếu
    file/profile -> trả dict rỗng/thiếu key, caller tự fallback về
    "không xếp hạng" — không giả vờ có trọng số."""
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, _RoutingWeights] = {}
    default = data.get("default")
    if default:
        result[_DEFAULT_PROFILE] = _RoutingWeights(**default)
    for name, weights in (data.get("profiles") or {}).items():
        result[name] = _RoutingWeights(**weights)
    return result


def _privacy_fit(provider_class: str) -> float:
    """P doc 06 §2.2 — "local=1.0, cloud=0.4", tính TĨNH từ provider.yaml::class,
    không cần lịch sử. `hybrid` xử lý như non-local (bảo thủ — tên gọi hàm ý vẫn
    gọi ra mạng)."""
    return _LOCAL_PRIVACY_FIT if provider_class == "local" else _NON_LOCAL_PRIVACY_FIT


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    open_until: Any = None  # datetime | None — Any để tránh import datetime chỉ cho type hint


@dataclass
class _Candidate:
    manifest: ProviderManifest
    priority: int
    eligible: bool
    reason: str | None = None
    tried: bool = field(default=False)
    error_code: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.manifest.provider_id,
            "priority": self.priority,
            "eligible": self.eligible,
        }
        if self.reason is not None:
            out["reason"] = self.reason
        if self.tried:
            out["tried"] = True
            out["error_code"] = self.error_code
        return out


@dataclass
class _AttemptOutcome:
    result: dict[str, Any] | None
    error: ProviderError | None
    # False = thử tiếp candidate kế (load thất bại, hoặc invoke lỗi retryable).
    # True = dừng hẳn chuỗi fallback (invoke lỗi KHÔNG retryable — lỗi input/logic,
    # thử provider khác không giúp gì, doc 06 §2.3). Tách khỏi `error.retryable` vì
    # load thất bại LUÔN phải thử tiếp, bất kể error đó tự gắn retryable gì.
    stop: bool = False


@dataclass
class _FallbackOutcome:
    result: dict[str, Any] | None = None
    chosen: str | None = None
    chosen_class: str | None = None
    error: ProviderError | None = None


class Router:
    """Trách nhiệm tầng apps/ (được phép import cả kernel/ và sdk/, doc 17 §1)."""

    def __init__(
        self,
        registry: Registry,
        events: EventBus,
        store: StateStore,
        resource_semaphores: dict[str, asyncio.Semaphore],
        workspace_root: Path,
        *,
        routing_policy_path: Path = _DEFAULT_ROUTING_POLICY_PATH,
        budget_policy_path: Path = _DEFAULT_BUDGET_POLICY_PATH,
        energy_policy_path: Path = _DEFAULT_ENERGY_POLICY_PATH,
        energy_engine: EnergyEngine | None = None,
    ) -> None:
        self._registry = registry
        self._events = events
        self._store = store
        self._resource_semaphores = resource_semaphores
        self._breakers: dict[str, _BreakerState] = {}
        self._cache = CacheStore(store, workspace_root / "cache")
        self._provider_stats = ProviderStats(store)
        self._routing_policy_path = routing_policy_path
        self._cost_engine = CostEngine(store, budget_policy_path)
        self._permission_guard = PermissionGuard(store, events)
        # `energy_engine` override — test truyền EnergyEngine với snapshot_fn
        # giả (không phụ thuộc tải máy THẬT lúc chạy CI, xem
        # apps/paosd/energy_engine.py::EnergyEngine.__init__). Production
        # luôn dùng nhánh else — đọc máy thật.
        self._energy_engine = energy_engine or EnergyEngine(energy_policy_path)

    async def call(
        self,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
        *,
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
        profile: str = _DEFAULT_PROFILE,
    ) -> tuple[dict[str, Any], str | None]:
        """Trả về `(result, chosen_provider_id)` — provider_id cần lộ ra ngoài
        từ P-M4-2 (ADR-0008): caller (`AgentContext.call()`) ghi lại provider
        đã phục vụ, để tự loại trừ đúng provider đó khi review (`exclude_provider`
        — "review PHẢI dùng provider khác generator", RSK-10).

        `contains_private_l3` (P-M5-4, doc 07 §6, doc 09 §7) — Privacy Filter:
        caller tự khai payload này có mang nội dung Memory L3 riêng tư hay
        không (KHÔNG phải Router tự quét nội dung — đủ cho quy mô hôm nay,
        chưa Agent nào đọc lại L3 để dùng thật, xem docs/backlog.md BL-015).
        `True` loại VÔ ĐIỀU KIỆN mọi candidate `provider_class == "cloud"`
        khỏi ứng viên, BẤT KỂ provider đó tự khai `privacy:` gì trong
        provider.yaml — xem `_classify()`. Mặc định `False`, không đổi hành
        vi của bất kỳ caller nào đã có hôm nay (8 Agent thật đều không truyền).

        `profile` (P-M6-3, doc 06 §2.2) — chọn bộ trọng số trong
        `policies/routing.yaml::profiles`. Mặc định `"default"` — khi đó Router
        TỰ nâng lên `"economy"` nếu chi tiêu hôm nay đã vượt `warn_at_pct`
        (`policies/budget.yaml`, doc 06 §6 "budget_used_pct>80 -> force_profile
        economy", P-M7-1). Truyền `profile` khác `"default"` rõ ràng luôn thắng
        — Router không ghi đè lựa chọn caller đã tự quyết định."""
        capability_id, version_str = capability_ref.split("@")
        version = int(version_str)
        manifests = self._registry.providers_for(capability_id, version)
        if not manifests:
            raise ProviderError(
                "NOT_FOUND",
                f"Không có provider nào đăng ký cho {capability_ref}",
                hint="Kiểm providers/*/provider.yaml có implements đúng capability này",
            )

        spec = self._registry.get_capability(capability_id, version)
        decision_id = ids.new_id("dec")
        candidates = self._classify(manifests, exclude_provider, contains_private_l3)
        await self._publish_privacy_blocked(decision_id, process_id, capability_ref, candidates)
        eligible = [c for c in candidates if c.eligible]
        task_class = normalize_task_class(payload.get("task_class"))
        effective_profile = await self._resolve_profile(process_id, profile)
        eligible, ranked = await self._rank_candidates(eligible, task_class, effective_profile)

        if spec.cacheable and spec.cache_key_fields and eligible:
            found = await self._lookup_cache(spec, capability_id, version, payload, eligible)
            if found is not None:
                hit, cache_key = found
                await self._write_cache_hit_decision(
                    decision_id, process_id, capability_ref, hit, cache_key
                )
                return hit.result, hit.provider_id

        fb = await self._run_fallback(
            eligible, decision_id, capability_ref, payload, process_id, task_class
        )

        cache_key: str | None = None
        if spec.cacheable and spec.cache_key_fields and fb.chosen and fb.chosen_class:
            cache_key = CacheStore.compute_key(
                capability_id, version, payload, spec.cache_key_fields, fb.chosen_class
            )

        rationale = self._rationale(candidates, fb.chosen, fb.error, ranked=ranked)
        await self._write_decision(
            decision_id,
            process_id,
            capability_ref,
            candidates,
            fb.chosen,
            rationale,
            inputs_hash=cache_key,
        )

        if fb.result is None:
            raise fb.error or ProviderError(
                "PROVIDER_DOWN",
                f"Không có provider nào khả dụng cho {capability_ref}",
                hint="Kiểm providers/*/provider.yaml: enabled, breaker, adapter",
                context={"capability": capability_ref},
            )

        if cache_key is not None and fb.chosen and fb.chosen_class:
            await self._cache.store(
                cache_key, capability_id, version, fb.chosen_class, fb.chosen, fb.result
            )
        return fb.result, fb.chosen

    async def _resolve_profile(self, process_id: str, profile: str) -> str:
        """doc 06 §6 "budget_used_pct>80 -> force_profile economy" (P-M7-1).
        Chỉ tự nâng khi caller KHÔNG tự chỉ định profile (`profile ==
        "default"`) — caller đã tự quyết định thì Router không ghi đè.
        `estimated_cost=0.0` — chỉ cần đọc `day_used_pct`, không kiểm 1 candidate
        cụ thể nào (đó là việc của `_check_budget()`)."""
        if profile != _DEFAULT_PROFILE:
            return profile
        check = await self._cost_engine.check_budget(process_id, 0.0)
        if check.day_used_pct > check.warn_at_pct:
            return "economy"
        return profile

    async def _rank_candidates(
        self, eligible: list[_Candidate], task_class: str, profile: str
    ) -> tuple[list[_Candidate], bool]:
        """doc 06 §2.2 — điểm = (w_q·Q̂ + w_p·P + w_r·R) / (w_q+w_p+w_r), RENORMALIZE
        trên đúng các số hạng THẬT SỰ có cho từng candidate (Q̂ cần `quality_n>=5`,
        R cần ≥1 lần thử ghi qua `record_attempt()`, P luôn tính được tĩnh từ
        `provider.yaml::class`). CHƯA có Ĉ/L̂ (Cost Engine/latency tracking = M7)
        — 2 số hạng đó vắng mặt hoàn toàn khỏi công thức này, không giả vờ bằng 0
        hay giá trị trung tính (một Ĉ=0 giả sẽ khiến Router tưởng MỌI provider
        miễn phí, sai lệch nghiêm trọng hơn là thiếu số hạng).

        CHỈ xếp lại khi ≥1 ứng viên có đủ Q̂ (`quality_n>=5`, đúng điều kiện
        P-M6-2) — cold start (100% test trước P-M6-2/3) giữ NGUYÊN thứ tự khai
        báo, 0 hành vi đổi."""
        policy = _load_routing_policy(self._routing_policy_path)
        weights = policy.get(profile) or policy.get(_DEFAULT_PROFILE)

        stats_by_provider: dict[str, Any] = {}
        for candidate in eligible:
            stats_by_provider[candidate.manifest.provider_id] = await self._provider_stats.get(
                candidate.manifest.provider_id, task_class
            )
        has_quality_signal = any(
            stat is not None and stat.has_enough_quality_samples and stat.quality_ewma is not None
            for stat in stats_by_provider.values()
        )
        if not has_quality_signal or weights is None:
            return eligible, False

        def _score(candidate: _Candidate) -> float:
            stat = stats_by_provider[candidate.manifest.provider_id]
            numerator = 0.0
            denominator = 0.0
            if (
                stat is not None
                and stat.has_enough_quality_samples
                and stat.quality_ewma is not None
            ):
                numerator += weights.w_q * stat.quality_ewma
                denominator += weights.w_q
            numerator += weights.w_p * _privacy_fit(candidate.manifest.provider_class)
            denominator += weights.w_p
            if stat is not None and stat.success_rate is not None:
                numerator += weights.w_r * stat.success_rate
                denominator += weights.w_r
            return numerator / denominator if denominator > 0 else 0.0

        ranked = sorted(eligible, key=lambda c: -_score(c))
        return ranked, True

    async def _run_fallback(
        self,
        eligible: list[_Candidate],
        decision_id: str,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
        task_class: str,
    ) -> _FallbackOutcome:
        outcome = _FallbackOutcome()
        for attempt, candidate in enumerate(eligible, start=1):
            if attempt > 1:
                await self._backoff(attempt)

            attempt_outcome = await self._attempt(
                candidate, capability_ref, payload, process_id, task_class
            )
            outcome.error = attempt_outcome.error
            if attempt_outcome.result is not None:
                outcome.result = attempt_outcome.result
                outcome.chosen = candidate.manifest.provider_id
                outcome.chosen_class = candidate.manifest.provider_class
                break
            if attempt_outcome.stop:
                break  # lỗi input/logic — thử provider khác không giúp gì (doc 06 §2.3)
            if attempt_outcome.error is not None and candidate.eligible:
                # Chỉ phát fallback-triggered cho lỗi invoke() retryable thật (candidate
                # còn eligible) — load thất bại đã ghi rõ LOAD_FAILED trong Decision
                # Record, không cần thêm event (doc 06 §2.3 nói về lỗi retryable ở invoke).
                next_candidate = eligible[attempt] if attempt < len(eligible) else None
                await self._publish_fallback(
                    decision_id,
                    process_id,
                    capability_ref,
                    candidate,
                    attempt_outcome.error,
                    next_candidate,
                )
        return outcome

    async def _attempt(
        self,
        candidate: _Candidate,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
        task_class: str,
    ) -> _AttemptOutcome:
        candidate.tried = True
        try:
            adapter = self._registry.load_adapter(candidate.manifest.provider_id)
        except PaosError as exc:
            candidate.eligible = False
            candidate.reason = f"LOAD_FAILED: {exc.message}"
            candidate.error_code = exc.code.value
            error = ProviderError(
                exc.code.value, exc.message, retryable=False, hint=exc.hint, context=exc.context
            )
            return _AttemptOutcome(None, error, stop=False)

        energy_outcome = await self._check_energy(candidate, capability_ref, process_id)
        if energy_outcome is not None:
            return energy_outcome

        budget_outcome = await self._check_budget(
            candidate, adapter, capability_ref, payload, process_id
        )
        if budget_outcome is not None:
            return budget_outcome

        call_ctx = CallContext(
            call_id=ids.new_id("call"),
            process_id=process_id,
            task_id=None,
            deadline=None,
            # budget_left CHỜ caller thật đọc tới — doc 06 §3.2 mốc 2 kiểm tra ở
            # tầng Router (CostEngine.check_budget()), chưa cần adapter tự đọc
            # cột này để tự quyết định gì (P4 — chưa 2 ca dùng thật).
            budget_left=None,
            privacy_class="private",
            cancel_token=asyncio.Event(),
        )
        try:
            async with self._hold_resources(adapter.manifest.resources):
                result = await adapter.invoke(capability_ref, payload, call_ctx)
        except ProviderError as exc:
            candidate.error_code = exc.code.value
            await self._record_failure(
                candidate.manifest.provider_id, task_class, retryable=exc.retryable
            )
            return _AttemptOutcome(None, exc, stop=not exc.retryable)

        await self._record_success(candidate.manifest.provider_id, task_class)
        usage = result.get("usage", {}) if isinstance(result.get("usage"), dict) else {}
        await self._cost_engine.record_actual(
            process_id=process_id,
            task_id=None,
            provider_id=candidate.manifest.provider_id,
            capability=capability_ref,
            manifest_cost=candidate.manifest.cost,
            usage=usage,
        )
        return _AttemptOutcome(result, None)

    async def _check_energy(
        self, candidate: _Candidate, capability_ref: str, process_id: str
    ) -> _AttemptOutcome | None:
        """doc 06 §4, ADR-0030 — CPU/pin/nhiệt THẬT (không phải chỉ đếm token
        nội bộ paosd, đã có từ M2-3 qua `_hold_resources()`). Trạng thái MÁY
        (không riêng 1 provider) nên mọi candidate của capability này bị loại
        GIỐNG NHAU — "không chạy đè" nghĩa là qua backoff/retry Task ĐÃ CÓ
        (đợi máy rảnh rồi thử lại), KHÔNG phải đổi sang candidate khác trong
        cùng 1 lượt gọi (khác `force_local` của budget, phân biệt được theo
        `provider_class` từng candidate). Phát `resource.wait.started`. CHƯA
        chuyển Process sang `WAITING` thật (BL-022, docs/backlog.md)."""
        check = self._energy_engine.check(capability_ref)
        if check.allowed:
            return None

        candidate.eligible = False
        candidate.reason = f"ENERGY_{check.reason}"
        await self._events.publish(
            EventType.RESOURCE_WAIT_STARTED.value,
            source="paosd.router",
            process_id=process_id,
            payload={
                "capability": capability_ref,
                "provider_id": candidate.manifest.provider_id,
                "reason": check.reason or "",
            },
        )
        error = ProviderError(
            "RESOURCE_EXHAUSTED",
            f"Máy đang bận ({check.reason}) — thử lại sau",
            retryable=True,
            hint="Xem policies/energy.yaml — điều chỉnh ngưỡng nếu quá thận trọng",
            context={"reason": check.reason or ""},
        )
        return _AttemptOutcome(None, error, stop=False)

    async def _check_budget(
        self,
        candidate: _Candidate,
        adapter: Any,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
    ) -> _AttemptOutcome | None:
        """doc 06 §3.2 mốc 2 — TRƯỚC invoke(), không phải sau. Trả `None` nếu
        được phép tiến hành; trả `_AttemptOutcome` (để `_attempt()` return ngay)
        nếu bị chặn. `action="ask"` DỪNG HẲN (`stop=True`, doc 19 P-M7-1 exit
        criteria "bị chặn và hỏi" — Phần 2 nối `PermissionGuard` để audit +
        phát `permission.approval.requested`). `force_local`/`block_cloud` chỉ
        loại CANDIDATE không phải local, thử tiếp candidate khác — đúng "vượt
        → BUDGET_EXCEEDED, thử fallback local" (doc 06 §3.2); candidate ĐÃ local
        thì cho qua luôn — mục đích của 2 hành động này chính là đẩy về local,
        chặn 1 candidate local vì lý do "ưu tiên local" là vô nghĩa."""
        try:
            estimate = await adapter.estimate(capability_ref, payload)
        except (PaosError, ProviderError):
            return None  # estimate() lỗi — không phải lỗi ngân sách, để invoke() tự xử lý

        check = await self._cost_engine.check_budget(process_id, estimate.cost)
        if check.allowed:
            return None
        if check.action != "ask" and candidate.manifest.provider_class == "local":
            return None

        candidate.eligible = False
        candidate.reason = (
            f"BUDGET_{(check.tier_name or '').upper()}_{(check.action or '').upper()}"
        )
        if check.action == "ask":
            # PermissionGuard CONFIRM (kernel/permission/guard.py) — audit_log +
            # phát permission.approval.requested. Mặc định luôn allowed=False
            # ("im lặng không bao giờ là đồng ý", doc 09 §3) — Router tự raise
            # BUDGET_EXCEEDED sau, approval_id chỉ để tra cứu sau này.
            decision = await self._permission_guard.check(
                "cost.budget_exceeded",
                actor=process_id,
                target=candidate.manifest.provider_id,
                detail={"tier": check.tier_name or "", "estimated_cost": estimate.cost},
            )
            error = ProviderError(
                "BUDGET_EXCEEDED",
                f"Vượt ngân sách {check.tier_name} — cần xác nhận trước khi tiếp tục",
                retryable=False,
                hint="Xem policies/budget.yaml::budgets — chờ kỳ ngân sách mới hoặc sửa policy",
                context={"tier": check.tier_name or "", "approval_id": decision.approval_id or ""},
            )
            return _AttemptOutcome(None, error, stop=True)
        error = ProviderError(
            "BUDGET_EXCEEDED",
            f"Vượt ngân sách {check.tier_name} — chỉ thử provider local",
            retryable=True,
            hint="Provider cloud tạm bị loại tới khi ngân sách reset, thử provider local",
            context={"tier": check.tier_name or ""},
        )
        return _AttemptOutcome(None, error, stop=False)

    @staticmethod
    async def _backoff(attempt: int) -> None:
        backoff = _BACKOFF_BASE_S * (2 ** (attempt - 2))
        jitter = random.uniform(0, _BACKOFF_JITTER_S)  # noqa: S311 — jitter thời gian chờ
        await asyncio.sleep(backoff + jitter)

    async def _publish_fallback(
        self,
        decision_id: str,
        process_id: str,
        capability_ref: str,
        failed: _Candidate,
        error: ProviderError,
        next_candidate: _Candidate | None,
    ) -> None:
        await self._events.publish(
            EventType.CAPABILITY_FALLBACK_TRIGGERED.value,
            source="paosd.router",
            process_id=process_id,
            payload={
                "capability": capability_ref,
                "failed_provider_id": failed.manifest.provider_id,
                "error_code": error.code.value,
                "next_provider_id": (next_candidate.manifest.provider_id if next_candidate else ""),
                "decision_id": decision_id,
            },
        )

    def _classify(
        self,
        manifests: list[ProviderManifest],
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for i, manifest in enumerate(manifests, start=1):
            reason: str | None = None
            if manifest.provider_id == exclude_provider:
                # P-M4-2 (ADR-0008, RSK-10) — review PHẢI dùng provider khác
                # đã sinh ra artifact đang bị chấm, chống "LLM tự khen mình".
                reason = "EXCLUDED_GENERATOR"
            elif not manifest.enabled:
                reason = "DISABLED"
            elif self._breaker_open(manifest.provider_id):
                reason = "BREAKER_OPEN"
            elif contains_private_l3 and manifest.provider_class == "cloud":
                # Privacy Filter (P-M5-4, doc 07 §6: "Memory L3 không bao giờ
                # được gửi tới provider class: cloud trừ khi Job có privacy:
                # shared và người dùng đã đồng ý" — Job.privacy_class thật CHƯA
                # tồn tại hôm nay, doc09 §7/M7/M8, nên quy tắc rút gọn AN TOÀN
                # cho hiện tại là: không bao giờ, không có nhánh "shared").
                # Dựa vào CLASS CẤU TRÚC (`provider.yaml::class`), KHÔNG dựa
                # vào tự khai `privacy:` của chính provider (nhánh elif dưới) —
                # một provider class:cloud tự khai `privacy: private` (đúng
                # hoặc GIAN DỐI) vẫn bị chặn tuyệt đối. Đây là phòng thủ theo
                # CHIỀU SÂU: PRIVACY_MISMATCH bên dưới chặn phần lớn qua tự
                # khai, nhưng không chống được provider khai sai — xem test
                # đối kháng tests/apps/paosd/test_privacy_filter.py.
                reason = "PRIVACY_L3_BLOCKED"
            elif manifest.privacy != "private":
                # Hôm nay CallContext.privacy_class luôn "private" (chưa có Job
                # policy thật — đó là M2-5/M7); provider khác "private" bị loại
                # vô điều kiện, đúng tinh thần P2 local-first.
                reason = "PRIVACY_MISMATCH"
            candidates.append(
                _Candidate(manifest=manifest, priority=i, eligible=reason is None, reason=reason)
            )
        return candidates

    async def _publish_privacy_blocked(
        self,
        decision_id: str,
        process_id: str,
        capability_ref: str,
        candidates: list[_Candidate],
    ) -> None:
        """1 event riêng cho MỖI candidate bị Privacy Filter chặn (P-M5-4) —
        Decision Record (`_write_decision`, ghi ngay sau khi biết `chosen`)
        đã bao trọn thông tin này trong `candidates_json`, nhưng doc 07 §6 đòi
        hỏi Trace lộ RÕ tín hiệu này (không bắt người đọc `paosctl explain`
        phải tự parse JSON để tìm), cùng tinh thần `_publish_fallback()` —
        payload CHỈ mang metadata (provider_id/capability/decision_id), KHÔNG
        bao giờ mang payload thật (chống rò rỉ chính thứ đang được chặn)."""
        for candidate in candidates:
            if candidate.reason == "PRIVACY_L3_BLOCKED":
                await self._events.publish(
                    EventType.PRIVACY_CLOUD_SEND_BLOCKED.value,
                    source="paosd.router",
                    process_id=process_id,
                    payload={
                        "capability": capability_ref,
                        "provider_id": candidate.manifest.provider_id,
                        "decision_id": decision_id,
                    },
                )

    def _breaker_open(self, provider_id: str) -> bool:
        state = self._breakers.get(provider_id)
        if state is None or state.open_until is None:
            return False
        return bool(clock.now() < state.open_until)

    async def _record_success(self, provider_id: str, task_class: str) -> None:
        self._breakers.pop(provider_id, None)  # về CLOSED
        await self._provider_stats.record_attempt(provider_id, task_class, succeeded=True)

    async def _record_failure(self, provider_id: str, task_class: str, *, retryable: bool) -> None:
        if not retryable:
            return  # lỗi input/logic không phản ánh sức khoẻ provider (doc 06 §2.3)
        await self._provider_stats.record_attempt(provider_id, task_class, succeeded=False)
        state = self._breakers.setdefault(provider_id, _BreakerState())
        if state.open_until is not None:
            # HALF_OPEN (open_until đã qua, vừa cho thử lại) mà VẪN lỗi — mở lại
            # ngay, không đếm lại từ đầu (đúng ngữ nghĩa circuit breaker chuẩn).
            state.open_until = clock.now() + timedelta(seconds=_BREAKER_OPEN_S)
            return
        state.consecutive_failures += 1
        if state.consecutive_failures >= _BREAKER_FAILURE_THRESHOLD:
            state.open_until = clock.now() + timedelta(seconds=_BREAKER_OPEN_S)

    @staticmethod
    def _rationale(
        candidates: list[_Candidate],
        chosen: str | None,
        final_error: ProviderError | None,
        *,
        ranked: bool = False,
    ) -> str:
        if chosen is not None:
            tried_before = [c for c in candidates if c.tried and c.manifest.provider_id != chosen]
            if tried_before:
                failed_ids = ", ".join(c.manifest.provider_id for c in tried_before)
                return f"{chosen}: fallback sau khi {failed_ids} lỗi retryable"
            if ranked:
                return (
                    f"{chosen}: quality_ewma cao nhất trong ứng viên đủ dữ liệu (n≥5, doc 06 §2.2)"
                )
            return f"{chosen}: ưu tiên #1 theo thứ tự khai báo, khả dụng"
        reasons = "; ".join(
            f"{c.manifest.provider_id}={c.reason}" for c in candidates if not c.eligible
        )
        detail = final_error.message if final_error else "không có ứng viên nào khả dụng"
        return f"Hết ứng viên cho capability này ({reasons or detail})"

    async def _write_decision(
        self,
        decision_id: str,
        process_id: str,
        capability_ref: str,
        candidates: list[_Candidate],
        chosen: str | None,
        rationale: str,
        *,
        inputs_hash: str | None,
    ) -> None:
        async def _insert(conn: aiosqlite.Connection) -> None:
            # redact() trên rationale/candidates_json trước khi ghi (doc 09 §6,
            # SEC-01) — rủi ro thật thấp (đều là chuỗi nội bộ: provider_id, nhãn lỗi
            # cố định) nhưng Decision Record là 1 trong các vị trí doc 19 P-M2-5 nêu
            # tên rõ ("candidates_json" ~ trace attrs), phòng thủ theo chiều sâu.
            await conn.execute(
                "INSERT INTO decisions(decision_id, process_id, scope, question, "
                "candidates_json, chosen, rationale, policy_version, inputs_hash, created_at) "
                "VALUES (?, ?, 'provider_selection', ?, ?, ?, ?, 'declared-priority@1', ?, ?)",
                (
                    decision_id,
                    process_id,
                    f"capability={capability_ref}",
                    redact(json.dumps([c.to_json() for c in candidates], ensure_ascii=False)),
                    chosen,
                    redact(rationale),
                    inputs_hash,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)

    async def _lookup_cache(
        self,
        spec: CapabilitySpec,
        capability_id: str,
        version: int,
        payload: dict[str, Any],
        eligible: list[_Candidate],
    ) -> tuple[CacheHit, str] | None:
        """Tra cache theo TỪNG class khác nhau còn xuất hiện trong candidate hợp lệ,
        đúng thứ tự ưu tiên, dừng ở lần trúng đầu tiên — không chỉ đoán theo candidate
        #1 (nếu #1 hôm nay là local nhưng lần trước nội dung này được phục vụ bởi
        cloud, đoán 1 lần sẽ bỏ lỡ oan; chi phí tra thêm gần như 0, PK lookup)."""
        seen_classes: set[str] = set()
        for candidate in eligible:
            provider_class = candidate.manifest.provider_class
            if provider_class in seen_classes:
                continue
            seen_classes.add(provider_class)
            cache_key = CacheStore.compute_key(
                capability_id, version, payload, spec.cache_key_fields, provider_class
            )
            hit = await self._cache.lookup(cache_key)
            if hit is not None:
                return hit, cache_key
        return None

    async def _write_cache_hit_decision(
        self,
        decision_id: str,
        process_id: str,
        capability_ref: str,
        hit: CacheHit,
        cache_key: str,
    ) -> None:
        """scope="cache_hit" mở rộng enum doc 03 dòng 139 (cột KHÔNG có CHECK constraint
        trong SQL — chỉ là quy ước ghi trong doc). Vẫn ghi Decision Record dù không có
        candidate nào được xét — bỏ qua sẽ để lại lỗ hổng trong `paosctl explain` cho
        đúng lượt gọi đó, vi phạm P5/ADR-0014 ("mọi Process sinh trace đầy đủ")."""

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO decisions(decision_id, process_id, scope, question, "
                "candidates_json, chosen, rationale, policy_version, inputs_hash, created_at) "
                "VALUES (?, ?, 'cache_hit', ?, NULL, ?, ?, 'declared-priority@1', ?, ?)",
                (
                    decision_id,
                    process_id,
                    f"capability={capability_ref}",
                    hit.provider_id,
                    redact(
                        f"Trúng cache — kết quả gốc từ {hit.provider_id} ({hit.provider_class})"
                    ),
                    cache_key,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)

    @asynccontextmanager
    async def _hold_resources(self, resource_names: list[str]) -> AsyncIterator[None]:
        """Giữ semaphore của TỪNG resource khai báo (bỏ qua tên không cấu hình
        dung lượng — coi như không giới hạn, doc 02 §3.2). Chuyển từ Runner
        sang đây ở P-M2-3: mỗi candidate trong chuỗi fallback có thể khai
        resources khác nhau, nên phải giữ token TRONG vòng lặp thử từng
        candidate, không phải 1 lần cho cả lượt gọi."""
        async with AsyncExitStack() as stack:
            for declared in resource_names:
                name = declared.partition(":")[0]
                sem = self._resource_semaphores.get(name)
                if sem is not None:
                    await stack.enter_async_context(sem)
            yield
