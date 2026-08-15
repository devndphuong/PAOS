"""Router — chọn provider cho 1 lượt gọi capability: ràng buộc cứng, chuỗi
fallback (backoff+jitter), circuit breaker, Decision Record (doc 06 §2.1/§2.3,
ADR-0014, doc 19 P-M2-3).

Ở apps/, KHÔNG ở kernel/: cần bắt sdk.provider.ProviderError, mà Kernel bị
cấm import sdk/ (MNT-06). Đây là nơi thay cho vòng lặp "chỉ thử provider ĐẦU
TIÊN nạp được" cũ ở Runner._make_call_capability() (P-M2-1) — giờ fallback
THẬT theo kết quả invoke(), không chỉ theo load().

CHƯA làm ở M2 (doc 19 P-M2-3): công thức chấm điểm + provider ranking (M6) —
chọn theo ĐÚNG thứ tự Registry.providers_for() trả về (ưu tiên khai báo, chưa
đảm bảo ổn định — đó là nợ riêng, chưa phải phạm vi lát này).

doc 06 §2.1 liệt kê 9 ràng buộc cứng — chỉ 3 làm được với dữ liệu ĐÃ CÓ hôm
nay (enabled, breaker OPEN, privacy_class). 6 mục còn lại (health FAIL, budget,
thiếu resource, offline_only, context/size) cần Cost/Time/Energy Engine (M7),
health poller, hoặc tokenizer — chưa tồn tại ở M2. HOÃN có ghi chú, không giả
vờ làm bằng giá trị hardcode luôn True/luôn qua.
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

from apps.paosd.cache_store import CacheHit, CacheStore
from kernel import clock, ids
from kernel.errors import PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.redact import redact
from kernel.registry.registry import CapabilitySpec, ProviderManifest, Registry
from kernel.state.db import StateStore
from sdk.provider import CallContext, ProviderError

# Cùng công thức với kernel/events/bus.py (M1-5a) — nhất quán, không bịa hằng số mới.
_BACKOFF_BASE_S = 0.05
_BACKOFF_JITTER_S = 0.03

_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_OPEN_S = 60.0


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
    ) -> None:
        self._registry = registry
        self._events = events
        self._store = store
        self._resource_semaphores = resource_semaphores
        self._breakers: dict[str, _BreakerState] = {}
        self._cache = CacheStore(store, workspace_root / "cache")

    async def call(
        self,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
        *,
        exclude_provider: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Trả về `(result, chosen_provider_id)` — provider_id cần lộ ra ngoài
        từ P-M4-2 (ADR-0008): caller (`AgentContext.call()`) ghi lại provider
        đã phục vụ, để tự loại trừ đúng provider đó khi review (`exclude_provider`
        — "review PHẢI dùng provider khác generator", RSK-10)."""
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
        candidates = self._classify(manifests, exclude_provider)
        eligible = [c for c in candidates if c.eligible]

        if spec.cacheable and spec.cache_key_fields and eligible:
            found = await self._lookup_cache(spec, capability_id, version, payload, eligible)
            if found is not None:
                hit, cache_key = found
                await self._write_cache_hit_decision(
                    decision_id, process_id, capability_ref, hit, cache_key
                )
                return hit.result, hit.provider_id

        fb = await self._run_fallback(eligible, decision_id, capability_ref, payload, process_id)

        cache_key: str | None = None
        if spec.cacheable and spec.cache_key_fields and fb.chosen and fb.chosen_class:
            cache_key = CacheStore.compute_key(
                capability_id, version, payload, spec.cache_key_fields, fb.chosen_class
            )

        rationale = self._rationale(candidates, fb.chosen, fb.error)
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

    async def _run_fallback(
        self,
        eligible: list[_Candidate],
        decision_id: str,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
    ) -> _FallbackOutcome:
        outcome = _FallbackOutcome()
        for attempt, candidate in enumerate(eligible, start=1):
            if attempt > 1:
                await self._backoff(attempt)

            attempt_outcome = await self._attempt(candidate, capability_ref, payload, process_id)
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

        call_ctx = CallContext(
            call_id=ids.new_id("call"),
            process_id=process_id,
            task_id=None,
            deadline=None,
            budget_left=None,
            privacy_class="private",
            cancel_token=asyncio.Event(),
        )
        try:
            async with self._hold_resources(adapter.manifest.resources):
                result = await adapter.invoke(capability_ref, payload, call_ctx)
        except ProviderError as exc:
            candidate.error_code = exc.code.value
            self._record_failure(candidate.manifest.provider_id, retryable=exc.retryable)
            return _AttemptOutcome(None, exc, stop=not exc.retryable)

        self._record_success(candidate.manifest.provider_id)
        return _AttemptOutcome(result, None)

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
        self, manifests: list[ProviderManifest], exclude_provider: str | None = None
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
            elif manifest.privacy != "private":
                # Hôm nay CallContext.privacy_class luôn "private" (chưa có Job
                # policy thật — đó là M2-5/M7); provider khác "private" bị loại
                # vô điều kiện, đúng tinh thần P2 local-first.
                reason = "PRIVACY_MISMATCH"
            candidates.append(
                _Candidate(manifest=manifest, priority=i, eligible=reason is None, reason=reason)
            )
        return candidates

    def _breaker_open(self, provider_id: str) -> bool:
        state = self._breakers.get(provider_id)
        if state is None or state.open_until is None:
            return False
        return bool(clock.now() < state.open_until)

    def _record_success(self, provider_id: str) -> None:
        self._breakers.pop(provider_id, None)  # về CLOSED

    def _record_failure(self, provider_id: str, *, retryable: bool) -> None:
        if not retryable:
            return  # lỗi input/logic không phản ánh sức khoẻ provider (doc 06 §2.3)
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
        candidates: list[_Candidate], chosen: str | None, final_error: ProviderError | None
    ) -> str:
        if chosen is not None:
            tried_before = [c for c in candidates if c.tried and c.manifest.provider_id != chosen]
            if tried_before:
                failed_ids = ", ".join(c.manifest.provider_id for c in tried_before)
                return f"{chosen}: fallback sau khi {failed_ids} lỗi retryable"
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
