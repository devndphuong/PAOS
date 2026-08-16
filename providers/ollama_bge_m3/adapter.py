"""Adapter mỏng cho Ollama, model embedding `bge-m3` (ADR-0015, P-M5-1).

File RIÊNG (không tái dùng chung class với `providers/ollama/adapter.py`, dù
logic HTTP gần giống hệt) — cùng lý do `sdk/rubric.py` không tái dùng
`kernel/workflow/expr.py` dù cùng triết lý parser: `OllamaAdapter` đọc
`provider.yaml` qua `Path(__file__).parent`, một CLASS DÙNG CHUNG cho 2 thư
mục provider sẽ CÙNG đọc 1 file, luôn trả sai model cho một trong hai. 2 file
nhỏ độc lập, mỗi cái rõ ràng đúng, rẻ hơn một cây kế thừa dễ vỡ.

`POST /api/embed` (doc Ollama, thay thế `/api/embeddings` cũ) — request
`{"model", "input"}`, response `{"model", "embeddings": [[...]], ...}` (LUÔN
là mảng lồng, kể cả 1 input — lấy `embeddings[0]`).
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import yaml

from sdk.provider import CallContext, Estimate, Health, ProviderError, load_provider_manifest

_MANIFEST_PATH = Path(__file__).parent / "provider.yaml"
_HEALTH_TIMEOUT_S = 3.0
_INVOKE_TIMEOUT_S = 60.0


def _load_model_tag() -> str:
    data = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    tag: str = data["model"]
    return tag


class OllamaEmbedAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self._base_url = base_url
        self._model = _load_model_tag()

    async def health(self) -> Health:
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
            if resp.status_code == HTTPStatus.OK:
                return Health(healthy=True)
            return Health(healthy=False, detail=f"HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            return Health(healthy=False, detail=str(exc))

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        # KHÔNG gọi ra ngoài (doc 04 §2.2) — ước lượng tĩnh dựa trên quality_hint.
        return Estimate(cost=0.0, latency_ms=500, confidence=0.6)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        if capability != "text.embed@1":
            raise ProviderError(
                "INVALID_INPUT",
                f"OllamaEmbedAdapter không hỗ trợ capability {capability}",
                hint="OllamaEmbedAdapter chỉ implement text.embed@1",
                context={"capability": capability},
            )
        body = {"model": self._model, "input": payload["text"]}

        try:
            async with httpx.AsyncClient(timeout=_INVOKE_TIMEOUT_S) as client:
                resp = await client.post(f"{self._base_url}/api/embed", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                f"Ollama không phản hồi sau {_INVOKE_TIMEOUT_S:.0f}s",
                retryable=True,
                hint="Lần gọi đầu Ollama nạp model có thể mất 30-60s — thử lại",
                context={"model": self._model},
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "PROVIDER_DOWN",
                f"Không kết nối được Ollama tại {self._base_url}",
                retryable=True,
                hint="Khởi động Ollama (`ollama serve`) hoặc bật provider dự phòng",
                context={"base_url": self._base_url},
            ) from exc

        if resp.status_code == HTTPStatus.NOT_FOUND:
            raise ProviderError(
                "NOT_FOUND",
                f"Model {self._model} chưa được pull",
                hint=f"Chạy `ollama pull {self._model}`",
                context={"model": self._model},
            )
        if resp.status_code != HTTPStatus.OK:
            raise ProviderError(
                "PROVIDER_DOWN",
                f"Ollama trả HTTP {resp.status_code}",
                retryable=True,
                hint="Kiểm `ollama serve` còn sống không, hoặc xem log Ollama",
                context={"status_code": resp.status_code},
            )

        data = resp.json()
        embedding = data["embeddings"][0]
        return {
            "embedding": embedding,
            "dim": len(embedding),
            "model": self._model,
            "usage": {"in_tokens": data.get("prompt_eval_count", 0)},
            "meta": {"model": self._model},
        }

    async def cancel(self, call_id: str) -> None:
        pass  # chưa nối ctx.cancel_token vào việc abort request httpx — nợ cùng BL-004
