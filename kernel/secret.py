"""Secret Manager — bản MVP (doc 09 §6, doc 19 P-M2-5).

Chỉ đọc từ biến môi trường `PAOS_SECRET_<PROVIDER>_<KEY>` — CHƯA làm OS keyring
hay `.paos/secrets.enc` mã hoá như doc 09 mô tả đầy đủ. Lý do: chưa có provider
cloud thật nào trong repo cần API key (2 provider hiện có đều chạy local) — xây kho bí
mật mã hoá cho 0 người dùng thật là trừu tượng hoá sớm (P4).
Nợ nâng cấp lên keyring/encrypted file khi có provider cloud đầu tiên cần nó.

`SecretRef` dạng `secret://<provider>/<key>` (doc 09 dòng 103) — KHÔNG lưu lại
giá trị đã đọc, mỗi lần gọi đọc lại từ nguồn (env var đọc lại rẻ, không cần cache).
"""

from __future__ import annotations

import os
import re

from kernel.errors import ErrorCode, PaosError

_REF_RE = re.compile(r"^secret://([a-z0-9_-]+)/([a-z0-9_-]+)$", re.IGNORECASE)


def resolve_secret(ref: str) -> str:
    """`secret://myservice/api_key` -> giá trị biến môi trường `PAOS_SECRET_MYSERVICE_API_KEY`.

    Raise PaosError(INVALID_INPUT) nếu ref sai định dạng, PaosError(NOT_FOUND)
    nếu biến môi trường chưa đặt — KHÔNG bao giờ trả chuỗi rỗng ngầm định."""
    match = _REF_RE.match(ref)
    if match is None:
        raise PaosError(
            ErrorCode.INVALID_INPUT,
            f"SecretRef sai định dạng: {ref!r}",
            hint="Đúng dạng phải là secret://<provider>/<key>, vd secret://myservice/api_key",
            context={"ref": ref},
        )
    provider, key = match.groups()
    env_name = f"PAOS_SECRET_{provider.upper()}_{key.upper()}"
    value = os.environ.get(env_name)
    if not value:
        raise PaosError(
            ErrorCode.NOT_FOUND,
            f"Chưa cấu hình secret cho {ref}",
            hint=f"Đặt biến môi trường {env_name} rồi thử lại",
            context={"ref": ref, "env_name": env_name},
        )
    return value
