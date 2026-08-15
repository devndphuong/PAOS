"""Voice Agent — 1 trong 3 nhánh song song của Video plugin phần 2 (doc 01 §3
UC1, doc 19 P-M3-4). Đi thẳng audio.tts@1 với nguyên văn script, cùng lý do
ImageAgent không cần bước lập kế hoạch riêng qua text.generate."""

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


class VoiceAgent:
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
        out = await self._ctx.call("audio.tts@1", {"text": plan.prompt, "voice": "default"})
        return ExecResult(data={"audio": out["audio"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        audio = result.data.get("audio", {})
        if not audio:
            return ReviewResult(passed=False, reason="EMPTY_AUDIO")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        audio = result.data["audio"]
        content = audio.get("placeholder_text", "")
        artifact = await self._ctx.write_artifact("voice", "voice.txt", content)
        await self._ctx.emit(
            "voice.created",
            {"artifact_id": artifact.artifact_id, "duration_sec": audio["duration_sec"]},
        )
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "VoiceAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
