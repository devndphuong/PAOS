"""MemoryStore — CRUD cho ký ức (doc 03 §3, doc 07, P-M5-1).

Chỉ lưu trữ cho L2/L3/L4 — L0 (RAM trong 1 Task) và L1 (RAM + checkpoint
trong 1 Process, doc 07 §1) đã có cơ chế riêng (context dict của Workflow
Engine, `AgentContext.checkpoint()`) — không tạo bảng song song cho 2 tầng
đó khi chưa có nhu cầu thật (P4). `tier` CHECK constraint ở migration 007 vẫn
cho phép cả 5 giá trị (đúng định nghĩa chung doc 07 §1), caller hôm nay chỉ
dùng L2/L3/L4.

DAO THUẦN — không tự gọi capability nào (KHÔNG tự gọi `text.embed@1`), cùng
vai trò `apps/paosd/cache_store.py`/`apps/paosd/artifact_store.py`: `write()`
nhận `embedding` đã tính sẵn (nếu có) từ caller, chỉ lo việc lưu. Việc tự
động "quan sát Event → ứng viên ký ức" (doc 07 §2, MemoryWriter nghe Event
Bus) là P-M5-2 — `write()` ở đây là API TRỰC TIẾP, được gọi tường minh."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from sqlite_vec import serialize_float32

from kernel import clock, ids
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.redact import redact, redact_deep
from kernel.state.db import StateStore

_SELECT_ITEM = (
    "SELECT memory_id, tier, scope_id, kind, key, content, meta_json, salience, "
    "confidence, source_json, expires_at, created_at, last_used_at, use_count "
    "FROM memory_items "
)


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    tier: str
    scope_id: str | None
    kind: str | None
    key: str | None
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    salience: float = 0.5
    confidence: float = 0.7
    source: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None
    created_at: str = ""
    last_used_at: str | None = None
    use_count: int = 0


def row_to_item(row: Any) -> MemoryItem:
    return MemoryItem(
        memory_id=row[0],
        tier=row[1],
        scope_id=row[2],
        kind=row[3],
        key=row[4],
        content=row[5],
        meta=json.loads(row[6]) if row[6] else {},
        salience=row[7],
        confidence=row[8],
        source=json.loads(row[9]) if row[9] else {},
        expires_at=row[10],
        created_at=row[11],
        last_used_at=row[12],
        use_count=row[13],
    )


class MemoryStore:
    def __init__(self, store: StateStore, events: EventBus) -> None:
        self._store = store
        self._events = events

    async def write(
        self,
        *,
        tier: str,
        content: str,
        scope_id: str | None = None,
        kind: str | None = None,
        key: str | None = None,
        meta: dict[str, Any] | None = None,
        salience: float = 0.5,
        confidence: float = 0.7,
        source: dict[str, Any] | None = None,
        expires_at: str | None = None,
        embedding: tuple[list[float], str] | None = None,
        process_id: str | None = None,
    ) -> MemoryItem:
        """`embedding` (vector, model_name) — đã tính sẵn từ caller (doc 08 §2
        nguyên tắc y hệt: DAO không tự gọi ra ngoài). Redact áp cho `content`/
        `meta`/`source` trước khi ghi (doc 09 §6, SEC-01, cùng quy ước
        `artifact_store.py`)."""
        memory_id = ids.new_id("mem")
        now = clock.now().isoformat()
        clean_content = redact(content)
        clean_meta = redact_deep(meta or {})
        clean_source = redact_deep(source or {})

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO memory_items(memory_id, tier, scope_id, kind, key, content, "
                "meta_json, salience, confidence, source_json, expires_at, created_at, "
                "last_used_at, use_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)",
                (
                    memory_id,
                    tier,
                    scope_id,
                    kind,
                    key,
                    clean_content,
                    json.dumps(clean_meta, ensure_ascii=False),
                    salience,
                    confidence,
                    json.dumps(clean_source, ensure_ascii=False),
                    expires_at,
                    now,
                ),
            )
            if embedding is not None:
                vector, model = embedding
                await conn.execute(
                    "INSERT INTO memory_vectors(memory_id, embedding, model, dim) "
                    "VALUES (?, ?, ?, ?)",
                    (memory_id, serialize_float32(vector), model, len(vector)),
                )

        await self._store.write(_insert)
        await self._events.publish(
            EventType.MEMORY_ITEM_WRITTEN.value,
            source="paosd.memory",
            payload={"memory_id": memory_id, "tier": tier, "key": key},
            process_id=process_id,
        )
        return MemoryItem(
            memory_id=memory_id,
            tier=tier,
            scope_id=scope_id,
            kind=kind,
            key=key,
            content=clean_content,
            meta=clean_meta,
            salience=salience,
            confidence=confidence,
            source=clean_source,
            expires_at=expires_at,
            created_at=now,
        )

    async def get(self, memory_id: str) -> MemoryItem | None:
        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(_SELECT_ITEM + "WHERE memory_id = ?", (memory_id,))
            return await cursor.fetchone()

        row = await self._store.read(_select)
        return row_to_item(row) if row is not None else None

    async def get_by_key(self, tier: str, key: str) -> MemoryItem | None:
        """Tra cứu chính xác (doc 07 §3 bước 1) — nhanh, tất định, ưu tiên cao nhất."""

        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(
                _SELECT_ITEM + "WHERE tier = ? AND key = ? ORDER BY created_at DESC LIMIT 1",
                (tier, key),
            )
            return await cursor.fetchone()

        row = await self._store.read(_select)
        return row_to_item(row) if row is not None else None

    async def list_by_tier(
        self, tier: str, *, scope_id: str | None = None, limit: int = 100
    ) -> list[MemoryItem]:
        async def _select(conn: aiosqlite.Connection) -> Any:
            if scope_id is None:
                cursor = await conn.execute(
                    _SELECT_ITEM + "WHERE tier = ? ORDER BY created_at DESC LIMIT ?",
                    (tier, limit),
                )
            else:
                cursor = await conn.execute(
                    _SELECT_ITEM
                    + "WHERE tier = ? AND scope_id = ? ORDER BY created_at DESC LIMIT ?",
                    (tier, scope_id, limit),
                )
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        return [row_to_item(r) for r in rows]

    async def touch(self, memory_id: str) -> None:
        """Cập nhật `last_used_at`/`use_count` khi 1 mục được truy hồi thật
        (doc 03 §3) — dùng cho recency boost ở lượt truy hồi SAU (doc 07 §3
        bước 4), không phải lượt này."""
        now = clock.now().isoformat()

        async def _update(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "UPDATE memory_items SET last_used_at = ?, use_count = use_count + 1 "
                "WHERE memory_id = ?",
                (now, memory_id),
            )

        await self._store.write(_update)

    async def update(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        confidence: float | None = None,
        scope_id: str | None = None,
    ) -> None:
        """Cập nhật TẠI CHỖ (UPDATE, không tạo hàng mới) — khác `write()` (luôn
        INSERT). Memory item không bất biến như Artifact (không có `supersedes`,
        doc 03 §3) — học sở thích (doc 07 §2.1, P-M5-2) là điều chỉnh dần
        `confidence`/`content`/`scope_id` của MỘT hàng theo thời gian, không
        phải tạo phiên bản mới. Tham số `None` nghĩa là GIỮ NGUYÊN (không phải
        "xoá") — chỉ cột có giá trị truyền vào mới bị ghi đè."""
        if content is None and confidence is None and scope_id is None:
            return
        clean_content = None if content is None else redact(content)

        async def _update(conn: aiosqlite.Connection) -> None:
            if clean_content is not None:
                await conn.execute(
                    "UPDATE memory_items SET content = ? WHERE memory_id = ?",
                    (clean_content, memory_id),
                )
            if confidence is not None:
                await conn.execute(
                    "UPDATE memory_items SET confidence = ? WHERE memory_id = ?",
                    (confidence, memory_id),
                )
            if scope_id is not None:
                await conn.execute(
                    "UPDATE memory_items SET scope_id = ? WHERE memory_id = ?",
                    (scope_id, memory_id),
                )

        await self._store.write(_update)
