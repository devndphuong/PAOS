"""Image Agent — 1 trong 3 nhánh song song của Video plugin phần 2 (doc 01
§3 UC1, doc 19 P-M3-4). KHÔNG gọi text.generate — đi thẳng image.generate@1
với mô tả rút từ script, không cần LLM lập kế hoạch ảnh ở lát này (đơn giản
hoá có chủ đích, chưa có bằng chứng cần bước lập kế hoạch riêng — P4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.agent import (
    AgentContext,
    AgentError,
    AgentManifest,
    Artifact,
    ExecResult,
    Plan,
    ReviewResult,
    ValidationResult,
    load_agent_manifest,
)
from sdk.provider import ErrorCode

_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


class ImageAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        if not inputs.get("script", "").strip():
            return ValidationResult(ok=False, reason="MISSING_SCRIPT")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        return Plan(prompt=f"Minh hoạ cho đoạn kịch bản: {inputs['script']}")

    async def execute(self, plan: Plan) -> ExecResult:
        out = await self._ctx.call("image.generate@1", {"prompt": plan.prompt, "count": 1})
        return ExecResult(data={"images": out["images"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        images = result.data.get("images", [])
        if len(images) == 0:
            return ReviewResult(passed=False, reason="EMPTY_IMAGES")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        artifacts = []
        for i, image in enumerate(result.data["images"]):
            content = image.get("placeholder_text", "")
            artifact = await self._ctx.write_artifact("image", f"image_{i}.txt", content)
            artifacts.append(artifact)
        await self._ctx.emit(
            "image.batch.created", {"artifact_ids": [a.artifact_id for a in artifacts]}
        )
        return artifacts

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "ImageAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
