"""Lớp lọc chạy trước mọi thao tác ghi log/event/artifact (doc 09 §6, SEC-01).

Bản tối giản Ngày 0 — 3 mẫu. Mở rộng thành thật (nhiều pattern hơn + test đối
kháng 20 vị trí) ở M2 (P-M2-5, doc 19 §5).
"""

from __future__ import annotations

import re

_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"key\s*=\s*[\"']?[A-Za-z0-9._-]{8,}[\"']?", re.IGNORECASE),
)

_MASK = "***REDACTED***"


def redact(text: str) -> str:
    """Thay thế mọi chuỗi khớp pattern secret đã biết bằng ***REDACTED***."""
    for pattern in _PATTERNS:
        text = pattern.sub(_MASK, text)
    return text
