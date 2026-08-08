"""SDK cho người viết Provider Adapter (doc 04 §2.2, doc 12 §4).

Đặt ở đây, KHÔNG phải kernel/, vì `providers/` bị cấm tuyệt đối import
kernel/ — kể cả GIÁN TIẾP (.importlinter contract "provider-la-driver" kiểm
cả đường phụ thuộc bắc cầu, không chỉ import trực tiếp). Vì vậy file này
KHÔNG được import bất kỳ thứ gì từ kernel/, kể cả kernel.errors — ErrorCode
và ProviderError ở đây là bản độc lập, không kế thừa PaosError, dù cùng hình
dạng (code/message/retryable/context/hint) và cùng 13 giá trị mã lỗi chuẩn
(doc 04 §1, doc 16 "mã lỗi chuẩn" — đây là từ vựng chung, không thuộc riêng
kernel). Trùng lặp nhỏ này là cái giá chấp nhận được để giữ ranh giới P1/P3.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import yaml


class ErrorCode(StrEnum):
    """Phải khớp NGUYÊN VĂN kernel.errors.ErrorCode (doc 04 §1) — 2 bản độc lập,
    cùng giá trị, vì sdk/ không được import kernel/ (xem docstring module)."""

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


class ProviderError(Exception):
    """Tương đương PaosError về hình dạng (doc 04 §1) nhưng độc lập import-wise.
    message tùy chọn — tự suy ra từ code nếu không truyền (doc 12 §4)."""

    def __init__(
        self,
        code: str | ErrorCode,
        message: str | None = None,
        *,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
        hint: str,
    ) -> None:
        resolved = ErrorCode(code) if isinstance(code, str) else code
        super().__init__(message or resolved.value)
        self.code = resolved
        self.message = message or resolved.value
        self.retryable = retryable
        self.context = context or {}
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "context": self.context,
                "hint": self.hint,
            }
        }


@dataclass(frozen=True)
class ProviderManifest:
    """doc 02 §5 — khai báo của một Provider Adapter, phía người viết provider."""

    provider_id: str
    implements: list[str]
    provider_class: str
    privacy: str
    cost: dict[str, Any]
    limits: dict[str, Any]
    resources: list[str]
    health_check: dict[str, Any]
    quality_hint: dict[str, Any]

    def implements_capability(self, capability_id: str, version: int) -> bool:
        return f"{capability_id}@{version}" in self.implements


def load_provider_manifest(path: Path) -> ProviderManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ProviderManifest(
        provider_id=data["id"],
        implements=data.get("implements", []),
        provider_class=data.get("class", "local"),
        privacy=data.get("privacy", "private"),
        cost=data.get("cost", {}),
        limits=data.get("limits", {}),
        resources=data.get("resources", []),
        health_check=data.get("health_check", {}),
        quality_hint=data.get("quality_hint", {}),
    )


@dataclass(frozen=True)
class Health:
    healthy: bool
    detail: str | None = None


@dataclass(frozen=True)
class Estimate:
    """KHÔNG được gọi ra ngoài — ước lượng thuần, không side-effect (doc 04 §2.2)."""

    cost: float
    latency_ms: int
    confidence: float


@dataclass(frozen=True)
class CallContext:
    call_id: str
    process_id: str | None
    task_id: str | None
    deadline: datetime | None
    budget_left: float | None
    privacy_class: str
    cancel_token: asyncio.Event
    on_progress: Callable[[float], None] | None = None


class ProviderAdapter(Protocol):
    manifest: ProviderManifest

    async def health(self) -> Health: ...

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate: ...

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        """Trả về đúng output_schema, hoặc raise ProviderError có mã chuẩn."""
        ...

    async def cancel(self, call_id: str) -> None: ...
