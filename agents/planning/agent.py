"""Planning Agent — bước đầu tiên của Video plugin (UC1, doc 01 §3, doc 19
P-M3-3): nhận văn bản nguồn, lập dàn ý ngắn cho video. Cùng hình dạng
SummarizeAgent (M0) — 6 bước, không có gì mới về kiến trúc, chỉ là agent
THẬT THỨ HAI chứng minh P4 (thêm agent không sửa agent cũ, không sửa Runner
ngoài 1 dòng đăng ký bảng tra cứu tĩnh, xem apps/paosd/runner.py)."""

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


class PlanningAgent:
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
        return Plan(prompt=prompt, task_class="video_planning_vi")

    async def execute(self, plan: Plan) -> ExecResult:
        out = await self._ctx.call(
            "text.generate@1", {"prompt": plan.prompt, "task_class": plan.task_class}
        )
        return ExecResult(data={"plan": out["text"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        # Gần rỗng có chủ đích (doc 19 P-M3-3, cùng lý do SummarizeAgent M0) —
        # chấm điểm thật là M4 (Review Agent, rubric).
        plan_text = result.data.get("plan", "")
        if len(plan_text) == 0:
            return ReviewResult(passed=False, reason="EMPTY_PLAN")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        artifact = await self._ctx.write_artifact("plan", "plan.txt", result.data["plan"])
        await self._ctx.emit("plan.created", {"artifact_id": artifact.artifact_id})
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        # manifest.checkpointable=False — xem docstring tương ứng ở SummarizeAgent.
        raise AgentError(
            ErrorCode.INTERNAL,
            "PlanningAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
