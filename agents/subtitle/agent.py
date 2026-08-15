"""Subtitle Agent — 1 trong 3 nhánh song song của Video plugin phần 2 (doc 01
§3 UC1, doc 19 P-M3-4). Đi thẳng text.transform@1 (provider CỤC BỘ THẬT,
không phải stub — providers/local_subtitle/adapter.py) — không cần LLM."""

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


class SubtitleAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        if not inputs.get("script", "").strip():
            return ValidationResult(ok=False, reason="MISSING_SCRIPT")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        return Plan(prompt=inputs["script"])

    async def execute(self, plan: Plan) -> ExecResult:
        out = await self._ctx.call("text.transform@1", {"text": plan.prompt, "format": "srt"})
        return ExecResult(data={"text": out["text"], "meta": out.get("meta", {})})

    async def review(self, result: ExecResult) -> ReviewResult:
        if len(result.data.get("text", "")) == 0:
            return ReviewResult(passed=False, reason="EMPTY_SUBTITLE")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        artifact = await self._ctx.write_artifact(
            "subtitle", "subtitle.srt", result.data["text"], mime="text/srt"
        )
        await self._ctx.emit("subtitle.created", {"artifact_id": artifact.artifact_id})
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "SubtitleAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
