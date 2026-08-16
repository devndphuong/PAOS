"""Kiểm giao thức JSON-RPC 2.0 qua stdio cho Plugin — encode/decode thuần
(ADR-0032, doc 19 P-M8-1)."""

from __future__ import annotations

import json

import pytest

from kernel.plugin.protocol import decode_line, encode_request


def test_encode_request_is_newline_terminated_json() -> None:
    line = encode_request(1, "health", {})
    assert line.endswith("\n")
    data = json.loads(line)
    assert data == {"jsonrpc": "2.0", "id": 1, "method": "health", "params": {}}


def test_decode_line_success_result() -> None:
    raw = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"healthy": True}})
    request_id, result, error = decode_line(raw)
    assert request_id == 7
    assert result == {"healthy": True}
    assert error is None


def test_decode_line_error_maps_paos_fields() -> None:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {
                "code": -32000,
                "message": "provider quá tải",
                "data": {
                    "paos_code": "RESOURCE_EXHAUSTED",
                    "retryable": True,
                    "hint": "thử lại sau",
                    "context": {"x": 1},
                },
            },
        }
    )
    request_id, result, error = decode_line(raw)
    assert request_id == 3
    assert result is None
    assert error is not None
    assert error.paos_code == "RESOURCE_EXHAUSTED"
    assert error.retryable is True
    assert error.hint == "thử lại sau"
    assert error.context == {"x": 1}


def test_decode_line_error_defaults_when_data_missing() -> None:
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "lỗi lạ"}})
    _, _, error = decode_line(raw)
    assert error is not None
    assert error.paos_code == "INTERNAL"
    assert error.retryable is False


def test_decode_line_rejects_non_jsonrpc() -> None:
    with pytest.raises(ValueError, match="JSON-RPC"):
        decode_line(json.dumps({"foo": "bar"}))


def test_decode_line_rejects_missing_result_and_error() -> None:
    with pytest.raises(ValueError, match="thiếu cả"):
        decode_line(json.dumps({"jsonrpc": "2.0", "id": 1}))
