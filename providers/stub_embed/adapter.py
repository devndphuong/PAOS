"""StubEmbedAdapter — tất định, thay `bge-m3` thật (ADR-0015) cho test/CI
offline (P-M5-1, cùng vai trò StubAdapter cho text.generate@1).

KHÔNG bịa vector ngẫu nhiên (mỗi lần gọi ra số khác nhau, không tái lập được)
— trung bình cộng một vector-theo-từ suy từ `sha256(từ)` cho mỗi từ trong
text, nên: (1) CÙNG text luôn ra CÙNG vector (test được), (2) 2 đoạn text
CHUNG từ có cosine similarity > 0 một cách có ý nghĩa (đủ để test ngưỡng lọc
cosine ở `apps/paosd/memory_retriever.py` mà không cần model thật)."""

from __future__ import annotations

import hashlib
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
_DIM = 32
_FAIL_ENV = "PAOS_STUB_FAIL"


def _word_vector(word: str, dim: int) -> list[float]:
    digest = hashlib.sha256(word.encode("utf-8")).digest()
    return [(digest[i % len(digest)] / 255.0) * 2 - 1 for i in range(dim)]


def _embed(text: str, dim: int = _DIM) -> list[float]:
    words = text.split() or [""]
    vectors = [_word_vector(w, dim) for w in words]
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


class StubEmbedAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=5, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        self._maybe_fail()
        text = payload["text"]
        embedding = _embed(text)
        return {
            "embedding": embedding,
            "dim": _DIM,
            "model": "stub.embed",
            "usage": {"in_tokens": len(text.split())},
            "meta": {"deterministic": True},
        }

    async def cancel(self, call_id: str) -> None:
        pass

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
            f"StubEmbedAdapter ép lỗi theo biến môi trường {_FAIL_ENV}",
            retryable=code in {ErrorCode.PROVIDER_TIMEOUT, ErrorCode.RESOURCE_EXHAUSTED},
            hint=f"Bỏ biến môi trường {_FAIL_ENV} để tắt chế độ ép lỗi",
            context={"forced_code": forced},
        )
