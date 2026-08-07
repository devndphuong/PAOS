"""Kiểm PaosError và ErrorCode (doc 04 §1, UX-01)."""

import pytest

from kernel.errors import ErrorCode, PaosError


def test_hint_is_required() -> None:
    with pytest.raises(TypeError):
        PaosError(ErrorCode.INTERNAL, "lỗi gì đó")  # type: ignore[call-arg]


def test_to_dict_matches_envelope_shape() -> None:
    err = PaosError(
        ErrorCode.PROVIDER_TIMEOUT,
        "ComfyUI không phản hồi",
        retryable=True,
        context={"provider_id": "comfyui.flux"},
        hint="Kiểm ComfyUI đang chạy ở localhost:8188",
    )
    d = err.to_dict()
    assert d == {
        "error": {
            "code": "PROVIDER_TIMEOUT",
            "message": "ComfyUI không phản hồi",
            "retryable": True,
            "context": {"provider_id": "comfyui.flux"},
            "hint": "Kiểm ComfyUI đang chạy ở localhost:8188",
        }
    }


def test_default_retryable_and_context() -> None:
    err = PaosError(ErrorCode.NOT_FOUND, "không tìm thấy", hint="kiểm lại id")
    assert err.retryable is False
    assert err.context == {}


def test_all_13_error_codes_exist() -> None:
    expected = {
        "INVALID_INPUT",
        "NOT_FOUND",
        "CONFLICT",
        "PERMISSION_DENIED",
        "BUDGET_EXCEEDED",
        "RESOURCE_EXHAUSTED",
        "PROVIDER_DOWN",
        "PROVIDER_TIMEOUT",
        "CONTENT_BLOCKED",
        "QUALITY_BELOW_THRESHOLD",
        "DEPENDENCY_FAILED",
        "CANCELLED",
        "INTERNAL",
    }
    assert {e.value for e in ErrorCode} == expected
