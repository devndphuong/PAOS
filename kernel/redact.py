"""Lớp lọc chạy trước mọi thao tác ghi log/event/artifact (doc 09 §6, SEC-01).

Bản tối giản Ngày 0 chỉ có 3 mẫu và chưa ai gọi tới — mở rộng thành thật ở M2
(P-M2-5, doc 19 §5): thêm pattern cho các dạng khoá phổ biến khác, và
`redact_deep()` đệ quy qua dict/list để áp được cho payload event, context lỗi,
candidates_json... không chỉ chuỗi phẳng. Wiring vào kernel/events/bus.py,
apps/paosd/runner.py, apps/paosd/router.py cùng lát này.

CỐ Ý KHÔNG thêm pattern "chuỗi hex/base64 dài bất kỳ" — dự án dùng sha256 làm
khoá nội dung khắp nơi (cache_key, artifact.sha256, decisions.inputs_hash);
một pattern như vậy sẽ redact nhầm dữ liệu KHÔNG phải bí mật, phá luôn tính
giải thích được (P5) mà chính những trường đó tồn tại để phục vụ.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"key\s*=\s*[\"']?[A-Za-z0-9._-]{8,}[\"']?", re.IGNORECASE),
    re.compile(r"(?:password|passwd|pwd)\s*=\s*[\"']?\S{6,}[\"']?", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key ID
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),  # Slack token
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
)

_MASK = "***REDACTED***"


def redact(text: str) -> str:
    """Thay thế mọi chuỗi khớp pattern secret đã biết bằng ***REDACTED***."""
    for pattern in _PATTERNS:
        text = pattern.sub(_MASK, text)
    return text


def redact_deep(value: Any) -> Any:
    """Đệ quy qua dict/list/tuple, áp redact() cho MỌI chuỗi tìm được — bất kể
    nằm sâu bao nhiêu trong cấu trúc lồng nhau (payload event, context lỗi,
    candidates_json...). Khoá dict giữ nguyên, chỉ giá trị bị lọc."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_deep(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_deep(v) for v in value)
    return value
