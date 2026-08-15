"""Agent đầu tiên của M0 — tóm tắt văn bản, đủ 6 bước vòng đời (doc 04 §3.1,
doc 19 P-M0-5). Hình dạng mọi agent tương lai sẽ sao chép — làm đủ ngay cả khi
một số bước gần rỗng (review() chỉ kiểm độ dài > 0)."""

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


class SummarizeAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        if not inputs.get("text", "").strip():
            return ValidationResult(ok=False, reason="MISSING_TEXT")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        template = self._ctx.prompt("v1")
        prompt = template.replace("{{text}}", inputs["text"])
        return Plan(prompt=prompt, task_class="summarize_vi")

    async def execute(self, plan: Plan) -> ExecResult:
        out = await self._ctx.call(
            "text.generate@1", {"prompt": plan.prompt, "task_class": plan.task_class}
        )
        return ExecResult(data={"summary": out["text"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        # Gần rỗng có chủ đích (doc 19 P-M0-5) — chấm điểm thật là M4 (Review Agent, rubric).
        summary = result.data.get("summary", "")
        if len(summary) == 0:
            return ReviewResult(passed=False, reason="EMPTY_SUMMARY")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        artifact = await self._ctx.write_artifact("summary", "summary.txt", result.data["summary"])
        await self._ctx.emit("summary.created", {"artifact_id": artifact.artifact_id})
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        # manifest.checkpointable=False (chạy < 30s, không bao giờ checkpoint) —
        # Runner (doc 19 P-M3-1) không được gọi hàm này; có mặt chỉ để khớp
        # Protocol Agent (doc 04 §3.1).
        raise AgentError(
            ErrorCode.INTERNAL,
            "SummarizeAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
