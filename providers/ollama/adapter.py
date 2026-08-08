"""Adapter mỏng cho Ollama (doc 19 P-M0-4, lát 4c) — MNT-02: <= 200 dòng.

health() timeout 3s, invoke() timeout 180s (lần gọi đầu Ollama nạp model mất
30-60s — R19). stream: false ở M0 (R20).
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
_INVOKE_TIMEOUT_S = 180.0


def _load_model_tag() -> str:
    data = yaml.safe_load(_MANIFEST_PATH.read_text(encoding="utf-8"))
    tag: str = data["model"]
    return tag


class OllamaAdapter:
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
        return Estimate(cost=0.0, latency_ms=15_000, confidence=0.6)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        if capability != "text.generate@1":
            raise ProviderError(
                "INVALID_INPUT",
                f"OllamaAdapter không hỗ trợ capability {capability}",
                hint="OllamaAdapter chỉ implement text.generate@1 ở M0",
                context={"capability": capability},
            )
        body = {
            "model": self._model,
            "prompt": payload["prompt"],
            "stream": False,
            "options": {
                "temperature": payload.get("temperature", 0.7),
                "num_predict": payload.get("max_tokens", 2048),
            },
        }
        if "system" in payload:
            body["system"] = payload["system"]

        try:
            async with httpx.AsyncClient(timeout=_INVOKE_TIMEOUT_S) as client:
                resp = await client.post(f"{self._base_url}/api/generate", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "PROVIDER_TIMEOUT",
                f"Ollama không phản hồi sau {_INVOKE_TIMEOUT_S:.0f}s",
                retryable=True,
                hint="Lần gọi đầu Ollama nạp model có thể mất 30-60s — thử lại; "
                "nếu vẫn treo, kiểm `ollama ps`",
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
        return {
            "text": data.get("response", ""),
            "usage": {
                "in_tokens": data.get("prompt_eval_count", 0),
                "out_tokens": data.get("eval_count", 0),
            },
            "meta": {"model": self._model},
        }

    async def cancel(self, call_id: str) -> None:
        pass  # chưa nối ctx.cancel_token vào việc abort request httpx — nợ ghi ở backlog
