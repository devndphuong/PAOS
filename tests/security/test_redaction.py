"""Cổng CI 4 — không secret nào lọt ra log/event/artifact (SEC-01, doc 09 §6)."""

from kernel.redact import redact


def test_redacts_openai_style_key() -> None:
    assert "sk-abcdefgh12345678" not in redact("api_key=sk-abcdefgh12345678")


def test_redacts_bearer_token() -> None:
    text = "Authorization: Bearer abcdef123456"
    assert "abcdef123456" not in redact(text)


def test_redacts_key_equals() -> None:
    assert "supersecretvalue" not in redact('key="supersecretvalue"')


def test_leaves_normal_text_untouched() -> None:
    text = "Job proc_01J8ZQ hoàn tất, chi phí 0đ"
    assert redact(text) == text
