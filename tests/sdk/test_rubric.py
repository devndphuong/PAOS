"""Kiểm sdk/rubric.py (doc 08 §2/§3, ADR-0008, P-M4-1) — engine thuần logic,
`call_llm` giả lập, không I/O thật (đúng lý do engine tách khỏi Review Agent)."""

from pathlib import Path

import pytest

from sdk.agent import AgentError
from sdk.rubric import (
    LlmJudgeVerdict,
    RubricCriterion,
    evaluate,
    evaluate_check,
    load_rubric,
)

_RUBRIC_PATH = Path(__file__).resolve().parents[2] / "rubrics" / "script.rubric.v1.yaml"

_GOOD_SCRIPT = " ".join(f"từ{i}" for i in range(180)) + "\n\n## CTA\nHãy đăng ký kênh."
_TOO_SHORT_SCRIPT = "quá ngắn\n\n## CTA\nHãy đăng ký kênh."
_NO_CTA_SCRIPT = " ".join(f"từ{i}" for i in range(180))


def test_load_rubric_parses_doc08_example() -> None:
    rubric = load_rubric(_RUBRIC_PATH)
    assert rubric.id == "script.rubric"
    assert rubric.version == 1
    assert rubric.applies_to == "script"
    assert rubric.threshold == 80
    assert rubric.fail_fast == ["length", "cta"]
    ids = [c.id for c in rubric.criteria]
    assert ids == ["logic", "redundancy", "length", "hook", "cta", "tone_match"]
    length = next(c for c in rubric.criteria if c.id == "length")
    assert length.kind == "deterministic"
    assert length.check == "150 <= word_count <= 200"
    logic = next(c for c in rubric.criteria if c.id == "logic")
    assert logic.scale == {0: "sai nghiêm trọng", 50: "có điểm mơ hồ", 100: "chặt chẽ"}


class TestEvaluateCheck:
    def test_chained_comparison(self) -> None:
        assert evaluate_check("150 <= word_count <= 200", {"word_count": 180}) is True
        assert evaluate_check("150 <= word_count <= 200", {"word_count": 100}) is False

    def test_has_section_matches_markdown_heading(self) -> None:
        ctx = {"has_section": lambda name: name in {"cta"}}
        assert evaluate_check("has_section('cta')", ctx) is True
        assert evaluate_check("has_section('outro')", ctx) is False

    def test_n_gram_overlap_detects_self_repetition(self) -> None:
        from sdk.rubric import _n_gram_overlap  # noqa: PLC0415 — kiểm hàm nội bộ trực tiếp

        repeated = "một hai ba " * 20
        assert _n_gram_overlap(repeated) > 0.5
        assert _n_gram_overlap("một hai ba bốn năm sáu bảy tám chín mười") == 0.0

    def test_unknown_identifier_raises_clear_error(self) -> None:
        with pytest.raises(AgentError) as exc_info:
            evaluate_check("khong_ton_tai > 5", {})
        assert "khong_ton_tai" in exc_info.value.message

    def test_malformed_expression_raises_clear_error(self) -> None:
        with pytest.raises(AgentError):
            evaluate_check("has_section('cta'", {"has_section": lambda n: True})


class TestLoadRubricValidation:
    def _write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "bad.rubric.v1.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_llm_criterion_in_fail_fast_rejected(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id: x\nversion: 1\napplies_to: script\nthreshold: 80\n"
            "criteria:\n  - id: a\n    weight: 1.0\n    kind: llm\n    question: q\n"
            "fail_fast: [a]\n",
        )
        with pytest.raises(AgentError) as exc_info:
            load_rubric(path)
        assert "fail_fast" in exc_info.value.message

    def test_deterministic_missing_check_rejected(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id: x\nversion: 1\napplies_to: script\nthreshold: 80\n"
            "criteria:\n  - id: a\n    weight: 1.0\n    kind: deterministic\n",
        )
        with pytest.raises(AgentError) as exc_info:
            load_rubric(path)
        assert "check" in exc_info.value.message

    def test_llm_missing_question_rejected(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id: x\nversion: 1\napplies_to: script\nthreshold: 80\n"
            "criteria:\n  - id: a\n    weight: 1.0\n    kind: llm\n",
        )
        with pytest.raises(AgentError) as exc_info:
            load_rubric(path)
        assert "question" in exc_info.value.message

    def test_unknown_kind_rejected(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id: x\nversion: 1\napplies_to: script\nthreshold: 80\n"
            "criteria:\n  - id: a\n    weight: 1.0\n    kind: vibe\n",
        )
        with pytest.raises(AgentError) as exc_info:
            load_rubric(path)
        assert "kind" in exc_info.value.message

    def test_fail_fast_references_unknown_criterion_rejected(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path,
            "id: x\nversion: 1\napplies_to: script\nthreshold: 80\n"
            "criteria:\n  - id: a\n    weight: 1.0\n    kind: deterministic\n    check: 'true'\n"
            "fail_fast: [khong_ton_tai]\n",
        )
        with pytest.raises(AgentError) as exc_info:
            load_rubric(path)
        assert "khong_ton_tai" in exc_info.value.message


async def _always_good_llm(
    criterion: RubricCriterion, text: str, extra: dict[str, str]
) -> LlmJudgeVerdict:
    return LlmJudgeVerdict(score=90.0, keep_note=f"{criterion.id} đang tốt")


async def _always_bad_llm(
    criterion: RubricCriterion, text: str, extra: dict[str, str]
) -> LlmJudgeVerdict:
    return LlmJudgeVerdict(
        score=30.0, issue=f"{criterion.id} có vấn đề", suggestion="Sửa lại", location="line 1"
    )


class TestEvaluateFailFast:
    async def test_fail_fast_rejects_without_calling_llm(self) -> None:
        rubric = load_rubric(_RUBRIC_PATH)
        calls = 0

        async def counting_llm(
            criterion: RubricCriterion, text: str, extra: dict[str, str]
        ) -> LlmJudgeVerdict:
            nonlocal calls
            calls += 1
            return LlmJudgeVerdict(score=90.0)

        result = await evaluate(rubric, _TOO_SHORT_SCRIPT, call_llm=counting_llm)

        assert calls == 0
        assert result.verdict == "reject"
        assert result.fail_fast_triggered == "length"
        assert result.llm_calls_skipped == 4  # logic, redundancy(hybrid), hook, tone_match
        assert result.failed[0].criterion_id == "length"

    async def test_fail_fast_on_missing_cta(self) -> None:
        rubric = load_rubric(_RUBRIC_PATH)
        result = await evaluate(rubric, _NO_CTA_SCRIPT, call_llm=_always_good_llm)
        assert result.verdict == "reject"
        assert result.fail_fast_triggered == "cta"


class TestEvaluateFull:
    async def test_all_criteria_good_passes(self) -> None:
        rubric = load_rubric(_RUBRIC_PATH)
        result = await evaluate(rubric, _GOOD_SCRIPT, call_llm=_always_good_llm)
        assert result.verdict == "pass"
        assert result.score >= rubric.threshold
        assert result.fail_fast_triggered is None
        assert result.llm_calls_skipped == 0
        assert result.failed == []
        assert len(result.keep) > 0

    async def test_bad_llm_scores_reject_with_structured_feedback(self) -> None:
        rubric = load_rubric(_RUBRIC_PATH)
        result = await evaluate(rubric, _GOOD_SCRIPT, call_llm=_always_bad_llm)
        assert result.verdict == "reject"
        feedback = result.to_feedback_json()
        assert feedback["verdict"] == "reject"
        assert isinstance(feedback["score"], int)
        assert all(
            {"criterion", "score", "issue", "suggestion", "location"} <= f.keys()
            for f in feedback["failed"]
        )
        failed_ids = {f["criterion"] for f in feedback["failed"]}
        assert "logic" in failed_ids and "hook" in failed_ids and "tone_match" in failed_ids

    async def test_hybrid_criterion_blends_check_and_llm(self) -> None:
        rubric = load_rubric(_RUBRIC_PATH)
        result = await evaluate(rubric, _GOOD_SCRIPT, call_llm=_always_good_llm)
        redundancy = next(b for b in result.breakdown if b.criterion_id == "redundancy")
        # check n_gram_overlap(script) < 0.18 đúng (text đa dạng từ) -> det=100,
        # llm=90 -> blend 50/50 = 95.
        assert redundancy.score == pytest.approx(95.0)
