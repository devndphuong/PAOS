"""Kiểm resolve_secret() — SecretRef MVP đọc biến môi trường (doc 09 §6, doc 19 P-M2-5)."""

from __future__ import annotations

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.secret import resolve_secret


def test_resolves_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAOS_SECRET_OPENAI_API_KEY", "sk-thattest12345678")
    assert resolve_secret("secret://openai/api_key") == "sk-thattest12345678"


def test_missing_env_var_raises_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAOS_SECRET_OPENAI_API_KEY", raising=False)
    with pytest.raises(PaosError) as exc_info:
        resolve_secret("secret://openai/api_key")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    "ref",
    [
        "not-a-secret-ref",
        "http://openai/api_key",
        "secret://openai",
        "secret://openai/",
        "secret:///api_key",
    ],
)
def test_malformed_ref_raises_invalid_input(ref: str) -> None:
    with pytest.raises(PaosError) as exc_info:
        resolve_secret(ref)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_empty_env_var_treated_as_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # Biến môi trường đặt = "" (khác hẳn "chưa đặt") vẫn phải coi là NOT_FOUND —
    # không bao giờ trả chuỗi rỗng ngầm định làm secret giả.
    monkeypatch.setenv("PAOS_SECRET_OPENAI_API_KEY", "")
    with pytest.raises(PaosError) as exc_info:
        resolve_secret("secret://openai/api_key")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_ref_is_case_insensitive_for_provider_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAOS_SECRET_OPENAI_API_KEY", "sk-thattest12345678")
    assert resolve_secret("secret://OpenAI/API_KEY") == "sk-thattest12345678"
