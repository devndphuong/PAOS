"""Cổng CI 4 — không secret nào lọt ra log/event/artifact (SEC-01, doc 09 §6,
doc 19 P-M2-5). Test ĐỐI KHÁNG: nhét secret vào 20 vị trí khác nhau rồi quét
đầu ra — 14 vị trí dạng dữ liệu (payload event, context PaosError, trace attrs
lồng nhau...) qua redact()/redact_deep() trực tiếp, 6 vị trí còn lại là tích
hợp THẬT qua EventBus/PermissionGuard (bảng events/audit_log thật, không mock)."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus
from kernel.permission.guard import PermissionGuard
from kernel.redact import redact, redact_deep
from kernel.state.db import StateStore
from sdk.provider import ProviderError

_OPENAI_KEY = "sk-abcdefgh12345678"
_BEARER = "Bearer abcdef123456xyz"
_AWS_KEY = "AKIAABCDEFGH12345678"
_SLACK_TOKEN = "xoxb-1234567890-abcdefghij"  # noqa: S105 — fixture giả cho test redact, không phải secret thật
_GITHUB_TOKEN = "ghp_abcdefghij1234567890"  # noqa: S105 — fixture giả cho test redact, không phải secret thật
_PASSWORD = 'password="supersecretvalue"'  # noqa: S105 — fixture giả cho test redact, không phải secret thật


# --- 1-4: pattern cơ bản (đã có từ Ngày 0, giữ lại) ---------------------------


def test_redacts_openai_style_key() -> None:
    assert _OPENAI_KEY not in redact(f"api_key={_OPENAI_KEY}")


def test_redacts_bearer_token() -> None:
    assert "abcdef123456xyz" not in redact(_BEARER)


def test_redacts_key_equals() -> None:
    assert "supersecretvalue" not in redact('key="supersecretvalue"')


def test_leaves_normal_text_untouched() -> None:
    text = "Job proc_01J8ZQ hoàn tất, chi phí 0đ"
    assert redact(text) == text


# --- 5-8: pattern mới mở rộng ở P-M2-5 ----------------------------------------


def test_redacts_password_equals() -> None:
    assert "supersecretvalue" not in redact(_PASSWORD)


def test_redacts_aws_access_key() -> None:
    assert _AWS_KEY not in redact(f"AWS_ACCESS_KEY_ID={_AWS_KEY}")


def test_redacts_slack_token() -> None:
    assert _SLACK_TOKEN not in redact(f"webhook token {_SLACK_TOKEN}")


def test_redacts_github_token() -> None:
    assert _GITHUB_TOKEN not in redact(f"git remote set-url with {_GITHUB_TOKEN}")


def test_redacts_pem_private_key_block() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIB...fake...\n-----END RSA PRIVATE KEY-----"
    result = redact(f"nội dung file:\n{pem}\nhết file")
    assert "MIIB" not in result
    assert "***REDACTED***" in result


# --- 9-11: vị trí trong chuỗi (đầu, cuối, nhiều loại cùng lúc) ----------------


def test_redacts_secret_at_start_of_string() -> None:
    assert _OPENAI_KEY not in redact(f"{_OPENAI_KEY} là key vừa sinh")


def test_redacts_secret_at_end_of_string() -> None:
    assert _OPENAI_KEY not in redact(f"key vừa sinh: {_OPENAI_KEY}")


def test_redacts_multiple_secret_types_in_same_string() -> None:
    text = f"log lỗi: {_OPENAI_KEY} và {_BEARER} và {_AWS_KEY}"
    result = redact(text)
    assert _OPENAI_KEY not in result
    assert "abcdef123456xyz" not in result
    assert _AWS_KEY not in result


# --- 12-16: redact_deep() qua cấu trúc lồng nhau (payload event, trace attrs) -


def test_redact_deep_walks_nested_dict() -> None:
    payload = {"outer": {"inner": f"api_key={_OPENAI_KEY}"}}
    result = redact_deep(payload)
    assert _OPENAI_KEY not in str(result)
    assert result["outer"]["inner"] == redact(f"api_key={_OPENAI_KEY}")


def test_redact_deep_walks_list_of_strings() -> None:
    trace_attrs = ["bình thường", f"password={_PASSWORD}"]
    result = redact_deep(trace_attrs)
    assert "supersecretvalue" not in str(result)


def test_redact_deep_walks_list_of_dicts() -> None:
    # dạng "trace attrs" doc 19 nêu tên — danh sách span, mỗi span 1 dict thuộc tính.
    spans = [{"name": "call", "attrs": {"header": _BEARER}}]
    result = redact_deep(spans)
    assert "abcdef123456xyz" not in str(result)


def test_redact_deep_preserves_dict_keys() -> None:
    payload = {"provider_id": "ollama.qwen2.5-14b", "secret": f"key={_OPENAI_KEY}"}
    result = redact_deep(payload)
    assert set(result.keys()) == {"provider_id", "secret"}
    assert result["provider_id"] == "ollama.qwen2.5-14b"  # không phải secret — giữ nguyên


def test_redact_deep_leaves_non_string_values_untouched() -> None:
    payload = {"count": 42, "ok": True, "ratio": 0.5, "nothing": None}
    assert redact_deep(payload) == payload


# --- 17-18: context/hint của PaosError, ProviderError -------------------------


def test_redact_deep_scrubs_paoserror_context() -> None:
    err = PaosError(
        ErrorCode.INTERNAL,
        f"lỗi kết nối: {_BEARER}",
        hint="thử lại",
        context={"upstream_response": f"Authorization: {_BEARER}"},
    )
    scrubbed = redact_deep(err.to_dict())
    assert "abcdef123456xyz" not in str(scrubbed)


def test_redact_deep_scrubs_providererror_to_dict() -> None:
    err = ProviderError(
        "PROVIDER_DOWN",
        f"key={_OPENAI_KEY} không hợp lệ",
        hint="kiểm lại secret",
        context={"raw": f"key={_OPENAI_KEY}"},
    )
    scrubbed = redact_deep(err.to_dict())
    assert _OPENAI_KEY not in str(scrubbed)


# --- 19-20: tích hợp THẬT — EventBus + PermissionGuard (không mock) ----------


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


async def _events_payload_json(store: StateStore, event_id: str) -> str:
    async def _select(conn: aiosqlite.Connection) -> str:
        cursor = await conn.execute(
            "SELECT payload_json FROM events WHERE event_id = ?", (event_id,)
        )
        row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    return await store.read(_select)


async def test_event_payload_redacted_before_persisted(store: StateStore) -> None:
    events = EventBus(store)
    envelope = await events.publish(
        "kernel.startup", "test", {"version": f"leak={_OPENAI_KEY}", "uptime": 0}
    )
    persisted = await _events_payload_json(store, envelope.event_id)
    assert _OPENAI_KEY not in persisted
    # envelope trong bộ nhớ (trả cho dispatch/subscriber) CŨNG đã lọc — 1 nguồn
    # sự thật duy nhất, không phải lọc riêng cho DB.
    assert _OPENAI_KEY not in str(envelope.payload)


async def test_audit_log_detail_redacted_before_persisted(store: StateStore) -> None:
    events = EventBus(store)
    guard = PermissionGuard(store, events)
    with pytest.raises(PaosError):
        await guard.check(
            "fs.hard_delete",
            actor="test-agent",
            target="workspace/scratch-target",
            detail={"reason": f"triggered by header {_BEARER}"},
        )

    async def _select(conn: aiosqlite.Connection) -> str:
        cursor = await conn.execute(
            "SELECT detail_json FROM audit_log WHERE action = 'fs.hard_delete'"
        )
        row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    detail_json = await store.read(_select)
    assert "abcdef123456xyz" not in detail_json
