"""LocalSubtitleAdapter — cắt văn bản thành cue phụ đề định dạng SRT, thuần
Python, tất định, không cần model/mạng (P-M3-4). Khác StubImageAdapter/
StubVoiceAdapter: đây là provider THẬT sẽ dùng luôn ở production, không phải
placeholder chờ hạ tầng chưa cài — subtitle không cần AI để làm đúng.

`_WORDS_PER_SECOND` giữ CÙNG giá trị với providers/stub_voice/adapter.py có
chủ đích: thời lượng cue phụ đề phải khớp nhịp đọc audio.tts ước lượng, nếu
không phụ đề sẽ trôi so với giọng đọc ngay cả khi cả 2 đều "đúng" theo cách
tính riêng của nó."""

from __future__ import annotations

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
_WORDS_PER_SECOND = 2.5
_MIN_CUE_DURATION_SEC = 0.8


def _srt_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _chunk_into_cues(text: str, max_chars_per_cue: int) -> list[str]:
    words = text.split()
    cues: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > max_chars_per_cue:
            cues.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        cues.append(" ".join(current))
    return cues


def _build_srt(cues: list[str]) -> tuple[str, float]:
    lines: list[str] = []
    cursor_sec = 0.0
    for i, cue_text in enumerate(cues, start=1):
        duration = max(_MIN_CUE_DURATION_SEC, len(cue_text.split()) / _WORDS_PER_SECOND)
        start, end = cursor_sec, cursor_sec + duration
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(cue_text)
        lines.append("")
        cursor_sec = end
    return "\n".join(lines), cursor_sec


class LocalSubtitleAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        text = payload["text"]
        max_chars = payload.get("max_chars_per_cue", 42)
        if not text.strip():
            raise ProviderError(
                ErrorCode.INVALID_INPUT,
                "text rỗng, không cắt được cue",
                hint="Truyền text khác rỗng",
            )
        cues = _chunk_into_cues(text, max_chars)
        srt_text, duration_sec = _build_srt(cues)
        return {
            "text": srt_text,
            "meta": {"cue_count": len(cues), "duration_sec": round(duration_sec, 2)},
        }

    async def cancel(self, call_id: str) -> None:
        pass
