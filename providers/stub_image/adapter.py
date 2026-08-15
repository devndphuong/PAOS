"""StubImageAdapter — tất định, không sinh pixel thật (P-M3-4). Cùng vai trò
`providers/stub/adapter.py` cho text.generate: hạ tầng đủ để test DAG/song
song/token tài nguyên mà không cần ComfyUI cài sẵn (máy dev GPU yếu — xem
docs/environment-baseline.md). `resources: [gpu:1]` trong provider.yaml vẫn
khai đúng như provider thật tương lai sẽ cần, nên Router giữ token y hệt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.provider import CallContext, Estimate, Health, load_provider_manifest

_MANIFEST_PATH = Path(__file__).parent / "provider.yaml"


class StubImageAdapter:
    manifest = load_provider_manifest(_MANIFEST_PATH)

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=10, confidence=1.0)

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        prompt = payload["prompt"]
        count = payload.get("count", 1)
        images = [
            {"placeholder_text": f"[stub:image.generate #{i + 1}] {prompt}", "seed": i}
            for i in range(count)
        ]
        return {"images": images, "meta": {"deterministic": True}}

    async def cancel(self, call_id: str) -> None:
        pass
