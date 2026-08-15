"""StubVoiceAdapter — tất định, chưa tổng hợp âm thanh thật (P-M3-4, cùng vai
trò StubImageAdapter). `duration_sec` KHÔNG bịa — ước lượng thật từ số từ /
tốc độ đọc trung bình (đo được, không phải hằng số ngẫu nhiên): edge-tts và
hầu hết TTS tiếng Việt tự nhiên rơi vào khoảng 2.2-2.8 từ/giây khi đọc bình
thường (không quá nhanh/chậm) — 2.5 là điểm giữa hợp lý, ghi rõ để khi có
provider thật (edge-tts) thay bằng số đo thật, không còn ước lượng."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.provider import CallContext, Estimate, Health, load_provider_manifest

_MANIFEST_PATH = Path(__file__).parent / "provider.yaml"
_WORDS_PER_SECOND = 2.5
_MIN_DURATION_SEC = 0.5


class StubVoiceAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=10, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        text = payload["text"]
        word_count = len(text.split())
        duration_sec = max(_MIN_DURATION_SEC, word_count / _WORDS_PER_SECOND)
        return {
            "audio": {
                "placeholder_text": f"[stub:audio.tts] {text}",
                "duration_sec": round(duration_sec, 2),
            },
            "meta": {"deterministic": True, "words_per_second": _WORDS_PER_SECOND},
        }

    async def cancel(self, call_id: str) -> None:
        pass
