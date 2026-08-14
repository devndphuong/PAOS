"""Provider tất định — nền cho PAOS_MODE=deterministic (doc 19 P-M0-4, lát 4b).

Dùng suốt dự án cho test không phụ thuộc model thật đang chạy.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from sdk.provider import (
    CallContext,
    ErrorCode,
    Estimate,
    Health,
    ProviderError,
    load_provider_manifest,
)

_MANIFEST_PATH = Path(__file__).parent / "provider.yaml"
_FAIL_ENV = "PAOS_STUB_FAIL"
_DELAY_ENV = "PAOS_STUB_DELAY_MS"


class StubAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    def __init__(self) -> None:
        # call_id -> event dừng sớm — chỉ dùng khi PAOS_STUB_DELAY_MS bật (doc 19 P-M2-2):
        # Conformance Suite cần ít nhất 1 provider có việc chạy đủ lâu để cancel() có ý
        # nghĩa thật. Bình thường StubAdapter không delay, invoke() trả ngay như cũ.
        self._cancel_events: dict[str, asyncio.Event] = {}

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=10, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self._maybe_fail()
        if capability != "text.generate@1":
            raise ProviderError(
                "INVALID_INPUT",
                f"StubAdapter không hỗ trợ capability {capability}",
                hint="StubAdapter chỉ implement text.generate@1 ở M0",
                context={"capability": capability},
            )
        await self._maybe_delay(ctx.call_id)
        return self._text_generate(payload)

    async def cancel(self, call_id: str) -> None:
        event = self._cancel_events.get(call_id)
        if event is not None:
            event.set()

    async def _maybe_delay(self, call_id: str) -> None:
        raw = os.environ.get(_DELAY_ENV)
        if not raw:
            return
        event = asyncio.Event()
        self._cancel_events[call_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=int(raw) / 1000)
        except TimeoutError:
            return  # hết giờ mà không bị hủy — trả kết quả bình thường
        finally:
            self._cancel_events.pop(call_id, None)
        raise ProviderError(
            "CANCELLED",
            "StubAdapter bị hủy giữa chừng qua cancel()",
            hint="Đây là hành vi mong đợi khi cancel() được gọi trong lúc invoke() đang chờ",
            context={"call_id": call_id},
        )

    def _maybe_fail(self) -> None:
        forced = os.environ.get(_FAIL_ENV)
        if not forced:
            return
        try:
            code = ErrorCode(forced)
        except ValueError as exc:
            raise RuntimeError(f"{_FAIL_ENV}={forced!r} không phải mã lỗi hợp lệ") from exc
        raise ProviderError(
            code,
            f"StubAdapter ép lỗi theo biến môi trường {_FAIL_ENV}",
            retryable=code in {ErrorCode.PROVIDER_TIMEOUT, ErrorCode.RESOURCE_EXHAUSTED},
            hint=f"Bỏ biến môi trường {_FAIL_ENV} để tắt chế độ ép lỗi",
            context={"forced_code": forced},
        )

    @staticmethod
    def _text_generate(payload: dict[str, Any]) -> dict[str, Any]:
        prompt = payload["prompt"]
        text = f"[stub:text.generate] {prompt}"
        return {
            "text": text,
            "usage": {"in_tokens": len(prompt.split()), "out_tokens": len(text.split())},
            "meta": {"deterministic": True},
        }
