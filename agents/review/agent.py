"""Review Agent (P-M4-2, doc 08 §3) — Agent thứ 8, chạy `sdk.rubric.evaluate()`
trên 1 artifact text. KHÔNG tự quyết định vòng lặp/escalation (đó là
`apps/paosd/workflow_runner.py::WorkflowRunner._run_self_correction`, doc 08
§4 — logic so sánh điểm giữa các vòng "phức tạp hơn 1 lượt chấm", ADR-0006 nói
rõ thứ đó thuộc về code, không phải YAML/Agent) — Agent này chỉ chấm ĐÚNG 1
lượt, trả điểm + feedback có cấu trúc, y hệt SummarizeAgent/ScriptAgent chỉ
làm đúng việc của 1 bước, không tự lặp."""

from __future__ import annotations

import json
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
from sdk.rubric import LlmJudgeVerdict, RubricCriterion, RubricReviewResult, RubricSpec, evaluate

_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


def _result_to_dict(review: RubricReviewResult) -> dict[str, Any]:
    return {
        "rubric_id": review.rubric_id,
        "rubric_version": review.rubric_version,
        "score": round(review.score, 2),
        "verdict": review.verdict,
        "fail_fast_triggered": review.fail_fast_triggered,
        "llm_calls_skipped": review.llm_calls_skipped,
        "breakdown": [
            {"criterion": b.criterion_id, "score": round(b.score, 2), "skipped": b.skipped}
            for b in review.breakdown
        ],
        "failed": [
            {
                "criterion": f.criterion_id,
                "score": round(f.score, 2),
                "issue": f.issue,
                "suggestion": f.suggestion,
                "location": f.location,
            }
            for f in review.failed
        ],
        "keep": review.keep,
    }


def _parse_judge_output(raw: str) -> LlmJudgeVerdict:
    """LLM judge BẮT BUỘC trả JSON có schema (doc 08 §3) — không cố "sửa giúp"
    JSON hỏng, thất bại rõ ràng (P8) để lộ ra vấn đề prompt/provider thật."""
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AgentError(
            ErrorCode.INVALID_INPUT,
            f"LLM judge không trả JSON hợp lệ: {exc}",
            hint="Kiểm prompt agents/review/prompts/v1.md — judge phải trả DUY NHẤT 1 object JSON",
        ) from exc
    if "score" not in data:
        raise AgentError(
            ErrorCode.INVALID_INPUT,
            "LLM judge trả JSON thiếu field 'score'",
            hint="Kiểm prompt agents/review/prompts/v1.md",
        )
    return LlmJudgeVerdict(
        score=float(data["score"]),
        issue=data.get("issue"),
        suggestion=data.get("suggestion"),
        keep_note=data.get("keep_note"),
    )


class ReviewAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        if not inputs.get("text", "").strip():
            return ValidationResult(ok=False, reason="MISSING_TEXT")
        if not isinstance(inputs.get("rubric"), RubricSpec):
            return ValidationResult(ok=False, reason="MISSING_RUBRIC")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        return Plan(
            prompt="",
            context={
                "text": inputs["text"],
                "rubric": inputs["rubric"],
                "exclude_provider": inputs.get("generator_provider"),
                "extra_context": inputs.get("extra_context", {}),
                "attempt": inputs.get("attempt", 1),
            },
        )

    async def execute(self, plan: Plan) -> ExecResult:
        rubric: RubricSpec = plan.context["rubric"]
        text: str = plan.context["text"]
        exclude_provider: str | None = plan.context.get("exclude_provider")
        extra_context: dict[str, str] = plan.context.get("extra_context", {})

        await self._ctx.emit("quality.review.started", {"rubric": f"{rubric.id}@{rubric.version}"})

        async def call_llm(
            criterion: RubricCriterion, criterion_text: str, extra: dict[str, str]
        ) -> LlmJudgeVerdict:
            template = self._ctx.prompt("v1")
            prompt = (
                template.replace("{{question}}", criterion.question or "")
                .replace("{{text}}", criterion_text)
                .replace("{{extra}}", json.dumps(extra, ensure_ascii=False) if extra else "")
            )
            out = await self._ctx.call(
                "text.generate@1",
                {"prompt": prompt, "task_class": "quality_judge"},
                exclude_provider=exclude_provider,
            )
            return _parse_judge_output(out["text"])

        review = await evaluate(rubric, text, call_llm=call_llm, extra_context=extra_context)
        return ExecResult(
            data={"review": _result_to_dict(review), "attempt": plan.context.get("attempt", 1)}
        )

    async def review(self, result: ExecResult) -> ReviewResult:
        # Cấu trúc luôn hợp lệ nếu execute() không raise (evaluate() đã tự bảo
        # đảm hình dạng RubricReviewResult) — không "chấm lại cái chấm", tránh
        # đệ quy vô nghĩa. Đây là kiểm tra CẤU TRÚC (đúng tinh thần review() của
        # mọi agent khác, vd SummarizeAgent chỉ kiểm len(summary) > 0).
        review = result.data.get("review", {})
        if "score" not in review or "verdict" not in review:
            return ReviewResult(passed=False, reason="MALFORMED_REVIEW_RESULT")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        review = result.data["review"]
        event_type = (
            "quality.review.passed" if review["verdict"] == "pass" else "quality.review.rejected"
        )
        payload: dict[str, Any] = {"score": round(review["score"])}
        if review["verdict"] == "pass":
            payload["breakdown"] = review["breakdown"]
        else:
            payload["failed_criteria"] = [f["criterion"] for f in review["failed"]]
            payload["feedback"] = {
                "score": round(review["score"]),
                "verdict": review["verdict"],
                "failed": review["failed"],
                "keep": review["keep"],
            }
        await self._ctx.emit(event_type, payload)

        attempt = result.data.get("attempt", 1)
        artifact = await self._ctx.write_artifact(
            "review",
            f"review_{review['rubric_id']}_v{review['rubric_version']}_attempt{attempt}.json",
            json.dumps(review, ensure_ascii=False, indent=2),
            mime="application/json",
        )
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "ReviewAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
