"""StubRenderAdapter — tất định, chưa gọi ffmpeg thật (P-M3-5, cùng vai trò
StubImageAdapter/StubVoiceAdapter). `duration_sec` KHÔNG bịa — bằng đúng
`duration_sec` của voice (video dài bằng lời đọc, quy tắc dựng video thật)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.provider import CallContext, Estimate, Health, load_provider_manifest

_MANIFEST_PATH = Path(__file__).parent / "provider.yaml"


class StubRenderAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=10, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        images = payload.get("images", [])
        voice = payload["voice"]
        subtitle = payload["subtitle"]
        summary = (
            f"[stub:video.render] {len(images)} ảnh + giọng đọc "
            f"({voice['duration_sec']}s) + phụ đề ({len(subtitle['text'])} ký tự)"
        )
        return {
            "video": {
                "placeholder_text": summary,
                "duration_sec": voice["duration_sec"],
                "image_count": len(images),
            },
            "meta": {"deterministic": True},
        }

    async def cancel(self, call_id: str) -> None:
        pass
