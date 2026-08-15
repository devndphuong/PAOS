"""Kiểm kernel/workflow/expr.py — parser tự viết, không eval (doc 04 §4, ADR-0027)."""

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.workflow.expr import evaluate_expr, is_expr, resolve, resolve_deep

_CTX = {
    "steps": {
        "detect": {"output": {"has_text_layer": False, "pages": 3}},
        "ocr": {"output": {}},
        "script": {"output": {"text": "nội dung script"}},
    },
    "inputs": {"pdf": "a.pdf", "duration": 75, "title": "MongoDB"},
}


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("steps.detect.output.has_text_layer == false", True),
        ("steps.detect.output.has_text_layer == true", False),
        ("steps.detect.output.pages == 3", True),
        ("steps.detect.output.pages > 1", True),
        ("steps.detect.output.pages >= 3", True),
        ("steps.detect.output.pages < 1", False),
        ("steps.detect.output.pages != 4", True),
        ('inputs.title == "MongoDB"', True),
        ("inputs.title == 'MongoDB'", True),
        ("inputs.missing == null", True),
    ],
)
def test_evaluate_comparisons(expr: str, expected: bool) -> None:
    assert evaluate_expr(expr, _CTX) is expected


def test_coalesce_falls_back_when_first_operand_missing() -> None:
    assert evaluate_expr("steps.ocr.output.text ?? steps.script.output.text", _CTX) == (
        "nội dung script"
    )


def test_coalesce_returns_none_when_all_operands_missing() -> None:
    assert evaluate_expr("steps.ocr.output.text ?? steps.ocr.output.other", _CTX) is None


def test_bare_path_resolves_to_raw_value() -> None:
    assert evaluate_expr("inputs.duration", _CTX) == 75


def test_missing_path_segment_resolves_to_none_not_error() -> None:
    assert evaluate_expr("steps.detect.output.no_such_field", _CTX) is None


def test_is_expr_detects_wrapper_only() -> None:
    assert is_expr("${inputs.pdf}") is True
    assert is_expr("plain string") is False
    assert is_expr(75) is False


def test_resolve_returns_literal_value_unchanged() -> None:
    assert resolve("plain string", _CTX) == "plain string"
    assert resolve(75, _CTX) == 75


def test_resolve_evaluates_wrapped_expression() -> None:
    assert resolve("${inputs.pdf}", _CTX) == "a.pdf"


def test_resolve_deep_recurses_into_dict_and_list() -> None:
    result = resolve_deep(
        {"file": "${inputs.pdf}", "tags": ["${inputs.title}", "static"], "n": 5}, _CTX
    )
    assert result == {"file": "a.pdf", "tags": ["MongoDB", "static"], "n": 5}


@pytest.mark.parametrize(
    "malicious",
    [
        "__import__('os').system('echo pwned')",
        "().__class__.__bases__",
        "open('/etc/passwd').read()",
        "1; 2",
        "steps.detect.output.pages + 1",  # không hỗ trợ số học — ngoài văn phạm
        "steps.detect.output.pages && steps.detect.output.pages",  # không hỗ trợ &&
    ],
)
def test_rejects_anything_outside_grammar(malicious: str) -> None:
    with pytest.raises(PaosError) as exc_info:
        evaluate_expr(malicious, _CTX)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_empty_expression_raises() -> None:
    with pytest.raises(PaosError):
        evaluate_expr("   ", _CTX)


def test_incompatible_type_comparison_raises_clear_error() -> None:
    with pytest.raises(PaosError) as exc_info:
        evaluate_expr("inputs.title < inputs.duration", _CTX)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT
