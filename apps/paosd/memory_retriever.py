"""MemoryRetriever — truy hồi lai (doc 07 §3, P-M5-1).

Triển khai 4/5 bước của doc 07 §3: (1) exact key lookup, (3) vector search,
(4) recency boost, (5) rerank + cắt theo ngân sách token. **Bước (2) — Knowledge
Graph walk (2 hop) — CHƯA LÀM**: Knowledge Graph chưa tồn tại (P-M5-3). Đây là
khoảng trống CÓ CHỦ ĐÍCH, không phải bỏ sót — ghi rõ ở đây thay vì giả vờ đủ 5
bước (P8, THẬT THÀ).

Vector search dùng bảng THƯỜNG `memory_vectors` + hàm `vec_distance_cosine()`
của `sqlite-vec` (không phải virtual table `vec0`) — xem addendum ADR-0015
(docs/15-adr-log.md) để biết lý do."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlite_vec import serialize_float32

from apps.paosd.memory_store import MemoryItem, MemoryStore, row_to_item
from apps.paosd.router import Router
from kernel.state.db import StateStore

_COSINE_SIMILARITY_THRESHOLD = 0.62  # doc 07 §3 bước 3
_TOP_K = 8  # doc 07 §3 bước 3
_MAX_DISTANCE = 1.0 - _COSINE_SIMILARITY_THRESHOLD  # vec_distance_cosine = 1 - cosine_similarity
_RECENCY_BOOST = 1.2  # doc 07 §3 bước 4
# doc 07 §3 không ghi con số cụ thể cho "gần đây" — quyết định module: 7 ngày,
# cùng tinh thần các hằng số nhỏ đã tự quyết trong sdk/rubric.py, sdk/eval.py.
_RECENCY_WINDOW = timedelta(days=7)
_DEFAULT_TOKEN_BUDGET = 2000  # doc 07 §3 bước 5
# process_id giả cho Decision Record khi lệnh gọi không gắn với Process thật.
_QUERY_PROCESS_ID = "memory_retrieval"

_VECTOR_SEARCH_SQL = (
    "WITH scored AS ("
    "  SELECT mi.memory_id, mi.tier, mi.scope_id, mi.kind, mi.key, mi.content, mi.meta_json,"
    "         mi.salience, mi.confidence, mi.source_json, mi.expires_at, mi.created_at,"
    "         mi.last_used_at, mi.use_count,"
    "         vec_distance_cosine(mv.embedding, ?) AS distance"
    "  FROM memory_vectors mv JOIN memory_items mi ON mi.memory_id = mv.memory_id"
    "{tier_filter}"
    ") SELECT * FROM scored WHERE distance <= ? ORDER BY distance LIMIT ?"
)


@dataclass(frozen=True)
class RetrievedMemory:
    item: MemoryItem
    score: float
    matched_via: str  # "exact_key" | "vector"


class MemoryRetriever:
    def __init__(self, store: StateStore, memory_store: MemoryStore, router: Router) -> None:
        self._store = store
        self._memory_store = memory_store
        self._router = router

    async def search(
        self,
        query: str,
        *,
        tier: str | None = None,
        key: str | None = None,
        token_budget: int = _DEFAULT_TOKEN_BUDGET,
        process_id: str | None = None,
    ) -> list[RetrievedMemory]:
        """`query` rỗng bỏ qua bước 3 (vector search) — chỉ còn bước 1 (nếu có
        `key`), hữu ích cho lookup thuần theo key không cần embed."""
        results: list[RetrievedMemory] = []
        seen_ids: set[str] = set()

        if key is not None and tier is not None:
            exact = await self._memory_store.get_by_key(tier, key)
            if exact is not None:
                results.append(RetrievedMemory(item=exact, score=1.0, matched_via="exact_key"))
                seen_ids.add(exact.memory_id)

        if query.strip():
            embed_result, _ = await self._router.call(
                "text.embed@1", {"text": query}, process_id or _QUERY_PROCESS_ID
            )
            vector_hits = await self._vector_search(embed_result["embedding"], tier=tier)
            results.extend(h for h in vector_hits if h.item.memory_id not in seen_ids)

        boosted = [self._apply_recency_boost(r) for r in results]
        final = self._rerank_and_cut(boosted, token_budget)
        for r in final:
            await self._memory_store.touch(r.item.memory_id)
        return final

    async def _vector_search(
        self, query_vector: list[float], *, tier: str | None
    ) -> list[RetrievedMemory]:
        sql = _VECTOR_SEARCH_SQL.format(tier_filter="  WHERE mi.tier = ?" if tier else "")
        params: tuple[Any, ...] = (
            (serialize_float32(query_vector), tier, _MAX_DISTANCE, _TOP_K)
            if tier
            else (serialize_float32(query_vector), _MAX_DISTANCE, _TOP_K)
        )

        async def _select(conn: Any) -> Any:
            cursor = await conn.execute(sql, params)
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        hits = []
        for row in rows:
            item = row_to_item(row[:14])
            distance = row[14]
            similarity = 1.0 - distance
            hits.append(RetrievedMemory(item=item, score=similarity, matched_via="vector"))
        return hits

    def _apply_recency_boost(self, retrieved: RetrievedMemory) -> RetrievedMemory:
        last_used = retrieved.item.last_used_at
        if last_used is None:
            return retrieved
        used_at = datetime.fromisoformat(last_used)
        if datetime.now(UTC) - used_at <= _RECENCY_WINDOW:
            return RetrievedMemory(
                item=retrieved.item,
                score=retrieved.score * _RECENCY_BOOST,
                matched_via=retrieved.matched_via,
            )
        return retrieved

    def _rerank_and_cut(
        self, results: list[RetrievedMemory], token_budget: int
    ) -> list[RetrievedMemory]:
        """doc 07 §3 bước 5 — sắp theo điểm giảm dần, cắt khi vượt ngân sách
        token (ước lượng thô bằng số từ, cùng quy ước `word_count` của
        `sdk/rubric.py` — không cần tokenizer thật ở bước xếp hạng thô này)."""
        ordered = sorted(results, key=lambda r: r.score, reverse=True)
        cut: list[RetrievedMemory] = []
        used_tokens = 0
        for r in ordered:
            cost = len(r.item.content.split())
            if cut and used_tokens + cost > token_budget:
                break
            cut.append(r)
            used_tokens += cost
        return cut
