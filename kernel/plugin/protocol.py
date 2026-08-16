"""Giao thức JSON-RPC 2.0 qua stdio cho Plugin — encode/decode thuần, không
I/O (I/O thật ở `kernel/plugin/process.py`), theo đúng ADR-0032.

Newline-delimited: mỗi message = 1 dòng JSON UTF-8 kết thúc `\\n`. 3 method
khớp `sdk.provider.ProviderAdapter`: `health`/`estimate`/`invoke` (ADR-0032 §Quyết
định) — module này KHÔNG biết method nào hợp lệ, chỉ lo đúng khung message,
danh sách method hợp lệ là việc của caller (`kernel/plugin/process.py`)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

_JSONRPC_VERSION = "2.0"


@dataclass(frozen=True)
class RpcError:
    """`data.paos_code`/`retryable`/`hint`/`context` ánh xạ THẲNG vào
    `sdk.provider.ProviderError` ở tầng gọi (`sdk/plugin_adapter.py`) — module
    này (kernel-safe) không được import `sdk.provider` để tự dựng exception đó."""

    code: int
    message: str
    paos_code: str = "INTERNAL"
    retryable: bool = False
    hint: str = ""
    context: dict[str, Any] = field(default_factory=dict)


def encode_request(request_id: int, method: str, params: dict[str, Any]) -> str:
    return (
        json.dumps(
            {"jsonrpc": _JSONRPC_VERSION, "id": request_id, "method": method, "params": params},
            ensure_ascii=False,
        )
        + "\n"
    )


def decode_line(line: str) -> tuple[int, dict[str, Any] | None, RpcError | None]:
    """Trả `(id, result, error)` — ĐÚNG 1 trong 2 (`result`/`error`) khác `None`.
    Raise `ValueError` nếu dòng không phải JSON-RPC 2.0 hợp lệ (không phải
    `RpcError` — đây là lỗi GIAO THỨC, khác lỗi NGHIỆP VỤ plugin trả về qua
    field `error` hợp lệ)."""
    data = json.loads(line)
    if not isinstance(data, dict) or data.get("jsonrpc") != _JSONRPC_VERSION or "id" not in data:
        raise ValueError(f"Không phải message JSON-RPC 2.0 hợp lệ: {line!r}")

    request_id = data["id"]
    if "error" in data:
        err = data["error"] or {}
        rpc_error = RpcError(
            code=int(err.get("code", -32000)),
            message=str(err.get("message", "lỗi không rõ từ plugin")),
            paos_code=str((err.get("data") or {}).get("paos_code", "INTERNAL")),
            retryable=bool((err.get("data") or {}).get("retryable", False)),
            hint=str((err.get("data") or {}).get("hint", "")),
            context=dict((err.get("data") or {}).get("context", {})),
        )
        return request_id, None, rpc_error
    if "result" not in data:
        raise ValueError(f"Message JSON-RPC 2.0 thiếu cả 'result' lẫn 'error': {line!r}")
    return request_id, data["result"], None
