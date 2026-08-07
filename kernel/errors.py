"""PaosError và 13 mã lỗi chuẩn (doc 04 §1).

Mọi lỗi hiển thị cho người dùng bắt buộc có hint (UX-01).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    PROVIDER_DOWN = "PROVIDER_DOWN"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    CONTENT_BLOCKED = "CONTENT_BLOCKED"
    QUALITY_BELOW_THRESHOLD = "QUALITY_BELOW_THRESHOLD"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"


class PaosError(Exception):
    """Lỗi chuẩn toàn hệ thống. `hint` là bắt buộc — "Đã xảy ra lỗi" không đạt UX-01.

    `trace_id` cố tình KHÔNG phải tham số ở đây — nó thuộc ngữ cảnh gọi (CallContext/
    request), không phải bản chất của lỗi, và được gắn vào lúc serialize ở tầng biên
    (API/log), không phải lúc raise.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
        hint: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.context = context or {}
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        """Khớp hình dạng envelope lỗi ở doc 04 §1 (thiếu trace_id — gắn ở tầng gọi)."""
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "context": self.context,
                "hint": self.hint,
            }
        }
