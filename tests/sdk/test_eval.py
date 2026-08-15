"""Kiểm sdk/eval.py (doc 08 §5/§7.5, P-M4-3) — engine thuần logic, không I/O
thật (đúng lý do đã áp dụng cho tests/sdk/test_rubric.py)."""

import json
from pathlib import Path

import pytest

from sdk.eval import (
    ConfigSummary,
    EvalCase,
    EvalOutcome,
    EvalReport,
    edit_rate,
    load_dataset,
    regression_violations,
)


class TestEditRate:
    def test_identical_text_is_zero(self) -> None:
        text = "một hai ba bốn năm"
        assert edit_rate(text, text) == 0.0

    def test_completely_different_text_is_clamped_to_one(self) -> None:
        assert edit_rate("một hai ba", "hoàn toàn khác biệt tất cả năm từ") == 1.0

    def test_partial_edit_is_between_zero_and_one(self) -> None:
        reference = "một hai ba bốn năm"
        candidate = "một hai ba bốn sáu"  # 1 từ khác trong 5 từ
        assert edit_rate(reference, candidate) == pytest.approx(0.2)

    def test_empty_reference_and_empty_candidate_is_zero(self) -> None:
        assert edit_rate("", "") == 0.0

    def test_empty_reference_nonempty_candidate_is_one(self) -> None:
        assert edit_rate("", "một hai") == 1.0

    def test_longer_candidate_still_clamped_to_one(self) -> None:
        reference = "một hai"
        candidate = " ".join(f"tu{i}" for i in range(50))
        assert edit_rate(reference, candidate) == 1.0


class TestLoadDataset:
    def test_parses_jsonl_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "ds.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "id": "c1",
                            "task_class": "script_writing_vi",
                            "inputs": {"plan": "dàn ý 1"},
                            "reference": "kịch bản 1",
                        }
                    ),
                    "",  # dòng trống phải bị bỏ qua
                    json.dumps(
                        {
                            "id": "c2",
                            "task_class": "script_writing_vi",
                            "inputs": {"plan": "dàn ý 2"},
                            "reference": "kịch bản 2",
                        }
                    ),
                ]
            ),
            encoding="utf-8",
        )
        cases = load_dataset(path)
        assert [c.id for c in cases] == ["c1", "c2"]
        assert cases[0].inputs == {"plan": "dàn ý 1"}
        assert cases[0].reference == "kịch bản 1"

    def test_missing_field_raises_with_line_number(self, tmp_path: Path) -> None:
        path = tmp_path / "ds.jsonl"
        path.write_text(json.dumps({"id": "c1", "task_class": "x"}), encoding="utf-8")
        with pytest.raises(ValueError, match=r"ds\.jsonl:1"):
            load_dataset(path)


def _outcome(config: str, case: str, quality: float, edit: float) -> EvalOutcome:
    return EvalOutcome(
        case_id=case,
        config_name=config,
        quality_score=quality,
        edit_rate=edit,
        cost=0.0,
        latency_ms=10.0,
    )


class TestEvalReport:
    def test_build_aggregates_per_config(self) -> None:
        outcomes = [
            _outcome("v1", "c1", 80.0, 0.20),
            _outcome("v1", "c2", 90.0, 0.10),
            _outcome("v2", "c1", 95.0, 0.05),
        ]
        report = EvalReport.build("script_writing_vi", "script.rubric@1", "2026-08-16", outcomes)
        by_name = {s.config_name: s for s in report.summaries}
        assert by_name["v1"].n == 2
        assert by_name["v1"].avg_quality == pytest.approx(85.0)
        assert by_name["v1"].avg_edit_rate == pytest.approx(0.15)
        assert by_name["v2"].n == 1
        assert by_name["v2"].avg_quality == pytest.approx(95.0)

    def test_to_markdown_contains_header_and_config_rows(self) -> None:
        outcomes = [_outcome("qwen+v4", "c1", 84.0, 0.18)]
        report = EvalReport.build("ds", "rubric@1", "2026-08-16", outcomes)
        md = report.to_markdown()
        assert "| Cấu hình | Quality | Edit rate | Cost | Latency |" in md
        assert "qwen+v4" in md
        assert "84" in md
        assert "18%" in md

    def test_json_roundtrip_preserves_outcomes(self) -> None:
        outcomes = [_outcome("v1", "c1", 80.0, 0.2), _outcome("v2", "c1", 90.0, 0.1)]
        report = EvalReport.build("ds", "rubric@1", "2026-08-16", outcomes)
        restored = EvalReport.from_json(report.to_json())
        assert restored.summaries == report.summaries
        assert restored.outcomes == report.outcomes


class TestRegressionViolations:
    def test_no_violations_when_quality_improves_and_edit_rate_drops(self) -> None:
        baseline = EvalReport.build("ds", "r@1", "d1", [_outcome("v1", "c1", 84.0, 0.18)])
        candidate = EvalReport.build("ds", "r@1", "d2", [_outcome("v1", "c1", 90.0, 0.10)])
        assert regression_violations(baseline, candidate) == []

    def test_flags_quality_drop_beyond_threshold(self) -> None:
        baseline = EvalReport.build("ds", "r@1", "d1", [_outcome("v1", "c1", 90.0, 0.10)])
        candidate = EvalReport.build("ds", "r@1", "d2", [_outcome("v1", "c1", 86.0, 0.10)])
        violations = regression_violations(baseline, candidate)
        assert len(violations) == 1
        assert "quality giảm" in violations[0]

    def test_flags_edit_rate_increase_beyond_threshold(self) -> None:
        baseline = EvalReport.build("ds", "r@1", "d1", [_outcome("v1", "c1", 90.0, 0.10)])
        candidate = EvalReport.build("ds", "r@1", "d2", [_outcome("v1", "c1", 90.0, 0.20)])
        violations = regression_violations(baseline, candidate)
        assert len(violations) == 1
        assert "edit_rate tăng" in violations[0]

    def test_small_drop_within_threshold_is_not_a_violation(self) -> None:
        baseline = EvalReport.build("ds", "r@1", "d1", [_outcome("v1", "c1", 90.0, 0.10)])
        candidate = EvalReport.build("ds", "r@1", "d2", [_outcome("v1", "c1", 88.0, 0.12)])
        assert regression_violations(baseline, candidate) == []

    def test_config_only_in_candidate_is_not_a_violation(self) -> None:
        baseline = EvalReport.build("ds", "r@1", "d1", [_outcome("v1", "c1", 90.0, 0.10)])
        candidate = EvalReport.build(
            "ds", "r@1", "d2", [_outcome("v1", "c1", 90.0, 0.10), _outcome("v2", "c1", 10.0, 0.99)]
        )
        assert regression_violations(baseline, candidate) == []


def test_eval_case_is_frozen() -> None:
    case = EvalCase(id="c1", task_class="script_writing_vi", inputs={"plan": "x"}, reference="y")
    with pytest.raises(AttributeError):
        case.id = "c2"  # type: ignore[misc]


def test_config_summary_equality_for_json_roundtrip() -> None:
    a = ConfigSummary("v1", 1, 90.0, 0.1, 0.0, 10.0)
    b = ConfigSummary("v1", 1, 90.0, 0.1, 0.0, 10.0)
    assert a == b
