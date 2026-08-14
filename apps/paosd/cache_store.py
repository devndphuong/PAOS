"""CacheStore — cache theo nội dung (content-addressed) cho kết quả invoke() thành
công (doc 03 §5.2, doc 19 P-M2-4).

cache_key = sha256(capability_id:version:normalized_input:provider_class). normalized_input
chỉ gồm field capability tự khai trong cache.json::key_fields (allowlist, doc 04 §2.1 mẫu
`cache_key_fields`) — không hash nguyên payload, tránh field không liên quan (vd task_class,
chỉ dùng cho routing/thống kê) làm trượt cache oan.

File thật nằm workspace_root/cache/<2 ký tự đầu key>/<key>.json, ghi nguyên tử (tmp + rename)
— đọc phải sống sót qua việc `cache/` bị xoá bất cứ lúc nào (doc 03 dòng 293, doc 09):
lookup() coi record DB mồ côi (file đã mất/hỏng) là cache miss, không raise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from kernel import clock
from kernel.state.db import StateStore


@dataclass(frozen=True)
class CacheHit:
    result: dict[str, Any]
    provider_id: str
    provider_class: str


def _row_to_paths(row: aiosqlite.Row | tuple[Any, ...]) -> tuple[str, str, str]:
    # (output_path, provider_id, provider_class) — khớp thứ tự cột SELECT ở _select().
    return row[0], row[1], row[2]


def _read_cache_file(path: Path) -> dict[str, Any] | None:
    """Đồng bộ, chạy qua asyncio.to_thread() — không block event loop (ASYNC, ADR-0024)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (FileNotFoundError, json.JSONDecodeError):
        return None  # record mồ côi hoặc file hỏng — coi như miss, KHÔNG raise


def _write_cache_file_atomic(path: Path, raw: str) -> int:
    """Đồng bộ, chạy qua asyncio.to_thread(). Ghi tmp + rename — nguyên tử, reader
    đồng thời không bao giờ thấy file dở dang."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    tmp_path.write_text(raw, encoding="utf-8")
    try:
        os.replace(tmp_path, path)
    except OSError:
        # Windows: os.replace() có thể ném PermissionError thoáng qua khi 2 writer
        # đua ghi CÙNG cache_key (content-addressed nên nội dung chắc chắn giống
        # nhau) — dọn tmp của mình, coi bên kia đã ghi xong là đủ. Nếu đích vẫn
        # chưa tồn tại thì đây là lỗi thật, ném lại.
        tmp_path.unlink(missing_ok=True)
        if not path.exists():
            raise
    return len(raw.encode("utf-8"))


class CacheStore:
    def __init__(self, store: StateStore, cache_dir: Path) -> None:
        self._store = store
        self._cache_dir = cache_dir

    @staticmethod
    def compute_key(
        capability_id: str,
        version: int,
        payload: dict[str, Any],
        key_fields: list[str],
        provider_class: str,
    ) -> str:
        normalized = {k: payload[k] for k in key_fields if k in payload}
        raw = json.dumps(normalized, sort_keys=True, ensure_ascii=True)
        digest_input = f"{capability_id}:{version}:{raw}:{provider_class}"
        return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()

    def _path_for(self, cache_key: str) -> Path:
        return self._cache_dir / cache_key[:2] / f"{cache_key}.json"

    async def lookup(self, cache_key: str) -> CacheHit | None:
        async def _select(conn: aiosqlite.Connection) -> tuple[str, str, str] | None:
            cursor = await conn.execute(
                "SELECT output_path, provider_id, provider_class FROM cache_entries "
                "WHERE cache_key = ?",
                (cache_key,),
            )
            row = await cursor.fetchone()
            return _row_to_paths(row) if row is not None else None

        row = await self._store.read(_select)
        if row is None:
            return None
        output_path, provider_id, provider_class = row

        result = await asyncio.to_thread(_read_cache_file, Path(output_path))
        if result is None:
            return None

        async def _record_hit(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ? "
                "WHERE cache_key = ?",
                (clock.now().isoformat(), cache_key),
            )

        await self._store.write(_record_hit)
        return CacheHit(result=result, provider_id=provider_id, provider_class=provider_class)

    async def store(
        self,
        cache_key: str,
        capability_id: str,
        version: int,
        provider_class: str,
        provider_id: str,
        result: dict[str, Any],
    ) -> None:
        path = self._path_for(cache_key)
        raw = json.dumps(result, ensure_ascii=False)
        size_bytes = await asyncio.to_thread(_write_cache_file_atomic, path, raw)

        async def _insert(conn: aiosqlite.Connection) -> None:
            # INSERT OR IGNORE: content-addressed nên trùng key = trùng nội dung — 2 Process
            # song song (M1-3a) cùng miss cache cho cùng nội dung không được vỡ UNIQUE.
            await conn.execute(
                "INSERT OR IGNORE INTO cache_entries(cache_key, capability_id, version, "
                "provider_class, provider_id, output_path, bytes, hit_count, created_at, "
                "last_hit_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)",
                (
                    cache_key,
                    capability_id,
                    version,
                    provider_class,
                    provider_id,
                    str(path),
                    size_bytes,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)
