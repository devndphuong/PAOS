"""Eval suite thật (doc 08 §7.5, P-M4-3) — chạy `agents/script/` THẬT (không
mock) qua 2 config prompt (`prompt_v1`/`prompt_v2`, đúng 2 file có sẵn trong
`agents/script/prompts/`) trên dataset `tests/eval/datasets/script_writing_vi.jsonl`,
chấm bằng `sdk/rubric.py` THẬT với `tests/eval/rubrics/script_eval.rubric.v1.yaml`
(đọc docstring file rubric đó để biết vì sao khác bản production), rồi gộp
bằng `sdk/eval.py` THẬT — cùng khuôn mẫu `_bare_ctx` đã dùng ở
tests/agents/review/test_review_agent.py (agent đơn lẻ, không cần dựng cả
daemon/Router như tests/apps/paosd/test_self_correction.py, vì eval harness
là công cụ phân tích ngoại tuyến, không phải đường chạy production).

Duy nhất phần GIẢ trong bài test này: `call_capability` (thay `text.generate@1`
thật) và `call_llm` judge (thay LLM judge thật) — cùng lý do mọi test agent
khác trong repo giả lập I/O bên ngoài. Cách giả judge: điểm mọi tiêu chí
llm/hybrid được suy trực tiếp từ `edit_rate(reference, text)` — văn bản càng
GẦN "kỳ vọng" của dataset thì judge càng cho điểm cao, giữ mối quan hệ
quality~edit_rate nhất quán logic thay vì bịa 2 con số độc lập.

Dataset chỉ có 6 mẫu (chưa phải 30-50 mẫu quy mô đầy đủ doc 08 §7.5) — xem
docstring tests/eval/rubrics/script_eval.rubric.v1.yaml."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agents.script.agent import ScriptAgent
from sdk.agent import AgentContext, Artifact
from sdk.eval import (
    EvalCase,
    EvalOutcome,
    EvalReport,
    edit_rate,
    load_dataset,
    regression_violations,
)
from sdk.rubric import LlmJudgeVerdict, RubricCriterion, RubricSpec, evaluate, load_rubric

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATASET = _REPO_ROOT / "tests" / "eval" / "datasets" / "script_writing_vi.jsonl"
_RUBRIC = _REPO_ROOT / "tests" / "eval" / "rubrics" / "script_eval.rubric.v1.yaml"
_SCRIPT_PROMPTS_DIR = _REPO_ROOT / "agents" / "script" / "prompts"

_CONFIGS = ("prompt_v1", "prompt_v2")


def _rough_draft(reference: str) -> str:
    """Bản nháp GIẢ LẬP cho config `prompt_v1` — mô phỏng lượt sinh đầu, còn
    thô: thay 1/3 số từ phần THÂN bài (giữ nguyên mục ## CTA, không phá
    fail_fast của tiêu chí `cta`). Số từ giữ nguyên -> không phá fail_fast
    `length`. Càng nhiều từ bị thay -> edit_rate với `reference` càng cao,
    đúng tinh thần "vòng 1 luôn kém hơn vòng đổi chiến lược" đã kiểm thật ở
    tests/apps/paosd/test_self_correction.py."""
    body, _, cta_section = reference.partition("\n\n## CTA")
    words = body.split()
    corrupted = [w if i % 3 != 0 else "bảnthô" for i, w in enumerate(words)]
    return " ".join(corrupted) + "\n\n## CTA" + cta_section


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_emit(event_type: str, payload: dict[str, Any]) -> None:
    pass


def _make_generate_call_capability(text: str) -> Any:
    async def call_capability(
        capability_ref: str,
        payload: dict[str, Any],
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> tuple[dict[str, Any], str | None]:
        return {"text": text, "usage": {}}, "stub.deterministic"

    return call_capability


async def _generate_one(
    tmp_path: Path, case: EvalCase, config_name: str
) -> tuple[str, float, float]:
    """1 lượt ScriptAgent THẬT (initialize->think->execute) cho 1 case x 1
    config. Trả (text, cost, latency_ms). `config_name` chọn prompt version
    qua ĐÚNG cơ chế `_force_alternate_prompt` mà self-correction loop dùng ở
    lượt cuối (apps/paosd/workflow_runner.py, doc 08 §4 quy tắc 4) — tái dùng
    input contract có sẵn của ScriptAgent, không thêm nhánh mới trong agent."""
    reference_variant = (
        case.reference if config_name == "prompt_v2" else _rough_draft(case.reference)
    )

    agent = ScriptAgent()
    ctx = AgentContext(
        process_id="eval_harness",
        task_id=None,
        workspace_dir=tmp_path,
        agent_id="script.agent",
        prompts_dir=_SCRIPT_PROMPTS_DIR,
        manifest=agent.manifest,
        persist_artifact=_noop_persist,
        call_capability=_make_generate_call_capability(reference_variant),
        emit_event=_noop_emit,
    )
    await agent.initialize(ctx)
    inputs: dict[str, Any] = dict(case.inputs)
    if config_name == "prompt_v2":
        inputs["_force_alternate_prompt"] = True

    started = time.perf_counter()
    plan = await agent.think(inputs)
    exec_result = await agent.execute(plan)
    latency_ms = (time.perf_counter() - started) * 1000
    return exec_result.data["text"], 0.0, latency_ms


def _make_judge(reference: str) -> Any:
    """Judge GIẢ — điểm mọi tiêu chí llm/hybrid suy từ độ gần `reference`
    (xem docstring module). `text` ở đây LUÔN là toàn bộ script (chữ ký
    `CallLlmJudge` của sdk/rubric.py truyền full text, không phải riêng từng
    tiêu chí)."""

    async def call_llm(
        criterion: RubricCriterion, text: str, extra: dict[str, str]
    ) -> LlmJudgeVerdict:
        score = round(100 * (1 - edit_rate(reference, text)))
        return LlmJudgeVerdict(score=float(score))

    return call_llm


async def _score_one(rubric: RubricSpec, text: str, reference: str) -> float:
    review = await evaluate(rubric, text, call_llm=_make_judge(reference))
    return review.score


@pytest.mark.eval
async def test_eval_suite_produces_report_comparing_prompt_v1_and_v2(tmp_path: Path) -> None:
    cases = load_dataset(_DATASET)
    rubric = load_rubric(_RUBRIC)
    assert len(cases) >= 5  # dataset seed đủ nhỏ để chạy nhanh, đủ lớn để không phải 1 mẫu

    outcomes: list[EvalOutcome] = []
    for case in cases:
        for config_name in _CONFIGS:
            text, cost, latency_ms = await _generate_one(tmp_path, case, config_name)
            score = await _score_one(rubric, text, case.reference)
            outcomes.append(
                EvalOutcome(
                    case_id=case.id,
                    config_name=config_name,
                    quality_score=score,
                    edit_rate=edit_rate(case.reference, text),
                    cost=cost,
                    latency_ms=latency_ms,
                )
            )

    report = EvalReport.build(
        dataset_id="script_writing_vi",
        rubric_id=f"{rubric.id}@{rubric.version}",
        generated_at=datetime.now(UTC).isoformat(),
        outcomes=outcomes,
    )

    by_config = {s.config_name: s for s in report.summaries}
    assert set(by_config) == set(_CONFIGS)
    assert by_config["prompt_v1"].n == len(cases)
    assert by_config["prompt_v2"].n == len(cases)

    # prompt_v2 (config "đổi chiến lược") phải THẬT SỰ tốt hơn — không phải số bịa.
    assert by_config["prompt_v2"].avg_edit_rate == 0.0  # sinh đúng y hệt "kỳ vọng"
    assert by_config["prompt_v1"].avg_edit_rate > 0.0
    assert by_config["prompt_v2"].avg_quality > by_config["prompt_v1"].avg_quality

    md = report.to_markdown()
    assert "prompt_v1" in md
    assert "prompt_v2" in md
    report_path = tmp_path / "runs" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")
    assert "Cấu hình" in report_path.read_text(encoding="utf-8")

    # Tự so báo cáo với chính nó -> không có regression (chứng minh hình dạng
    # EvalReport thật do harness sinh ra tương thích với regression_violations,
    # phần LOGIC quy tắc đã kiểm kỹ ở tests/sdk/test_eval.py).
    assert regression_violations(report, report) == []


@pytest.mark.eval
async def test_generate_selects_v2_prompt_template_when_config_is_prompt_v2(
    tmp_path: Path,
) -> None:
    """Xác nhận config `prompt_v2` THẬT SỰ chọn agents/script/prompts/v2.md
    (không phải chỉ tình cờ điểm cao) — cùng cách kiểm
    tests/apps/paosd/test_self_correction.py dùng cụm từ đặc trưng mỗi bản."""
    case = load_dataset(_DATASET)[0]
    captured: dict[str, str] = {}

    agent = ScriptAgent()
    ctx = AgentContext(
        process_id="eval_harness",
        task_id=None,
        workspace_dir=tmp_path,
        agent_id="script.agent",
        prompts_dir=_SCRIPT_PROMPTS_DIR,
        manifest=agent.manifest,
        persist_artifact=_noop_persist,
        call_capability=_make_generate_call_capability("bất kỳ"),
        emit_event=_noop_emit,
    )
    await agent.initialize(ctx)

    plan_v1 = await agent.think(dict(case.inputs))
    plan_v2 = await agent.think({**case.inputs, "_force_alternate_prompt": True})
    captured["v1"] = plan_v1.prompt
    captured["v2"] = plan_v2.prompt

    assert "người viết kịch bản video ngắn" in captured["v1"]
    assert "biên kịch cấp cao" in captured["v2"]
