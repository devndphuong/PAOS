"""Script Agent — bước 2 của Video plugin (UC1, doc 01 §3, doc 19 P-M3-3):
nhận `plan` (output của PlanningAgent qua Workflow, không phải gọi trực tiếp
— P2, Agent không được gọi Agent khác), viết lời thoại đầy đủ. Cùng hình
dạng SummarizeAgent/PlanningAgent — agent THẬT THỨ BA, tiếp tục chứng minh
P4 (0 dòng sửa PlanningAgent để thêm agent này)."""

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


class ScriptAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        if not inputs.get("plan", "").strip():
            return ValidationResult(ok=False, reason="MISSING_PLAN")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        template = self._ctx.prompt("v1")
        prompt = template.replace("{{plan}}", inputs["plan"])
        return Plan(prompt=prompt, task_class="script_writing_vi")

    async def execute(self, plan: Plan) -> ExecResult:
        out = await self._ctx.call(
            "text.generate@1", {"prompt": plan.prompt, "task_class": plan.task_class}
        )
        return ExecResult(data={"text": out["text"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        # Gần rỗng có chủ đích, cùng lý do PlanningAgent — chấm điểm thật là M4.
        script_text = result.data.get("text", "")
        if len(script_text) == 0:
            return ReviewResult(passed=False, reason="EMPTY_SCRIPT")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        artifact = await self._ctx.write_artifact("script", "script.txt", result.data["text"])
        await self._ctx.emit("script.created", {"artifact_id": artifact.artifact_id})
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "ScriptAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
