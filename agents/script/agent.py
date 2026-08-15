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
        # "_force_alternate_prompt" (P-M4-2, doc 08 §4 quy tắc 4) — self-correction
        # loop (apps/paosd/workflow_runner.py::_run_self_correction) BẮT BUỘC đổi
        # chiến lược ở lượt thử cuối, không lặp lại y hệt prompt cũ. Input thường
        # (không qua loop) không set field này -> hành vi cũ nguyên vẹn.
        version = "v2" if inputs.get("_force_alternate_prompt") else "v1"
        template = self._ctx.prompt(version)
        prompt = template.replace("{{plan}}", inputs["plan"])
        # "_prior_feedback" (vòng 2+ của self-correction loop) — feedback có cấu
        # trúc từ Review Agent vòng trước (doc 08 §3 "keep" quan trọng ngang
        # "failed", tránh viết lại từ đầu và làm hỏng phần đang tốt).
        prior_feedback = inputs.get("_prior_feedback")
        if prior_feedback:
            prompt += (
                f"\n\nGóp ý từ vòng chấm trước (sửa đúng phần này, GIỮ phần đã tốt):\n"
                f"{prior_feedback}"
            )
        return Plan(prompt=prompt, task_class="script_writing_vi")

    async def execute(self, plan: Plan) -> ExecResult:
        # P3 (doc 04 §2.2): Agent không bao giờ được biết ai phục vụ mình, kể
        # cả chỉ để CHUYỂN TIẾP mù quáng — không đọc thuộc tính "ai vừa phục
        # vụ" nào của self._ctx ở đây. Orchestrator self_correction
        # (apps/paosd/workflow_runner.py, tầng ĐƯỢC PHÉP biết) tự đọc thẳng
        # thuộc tính đó từ chính AgentContext nó dựng ra cho lượt gọi này —
        # không cần Script Agent tham gia đường dữ liệu đó.
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
