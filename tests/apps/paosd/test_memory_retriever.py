"""Kiểm MemoryRetriever — truy hồi lai (doc 07 §3, P-M5-1).

`_FakeRouter` thay Router thật — chỉ cần đúng chữ ký `call()`, trả embedding
GIẢ có kiểm soát được (không qua Registry/Provider thật), để test riêng logic
xếp hạng/recency/budget mà không phụ thuộc `sqlite-vec`/stub adapter thật.
Ngưỡng cosine/vector search (cần `vec_distance_cosine` thật) đã kiểm qua HTTP
end-to-end ở tests/apps/paosd/test_app.py — ở đây tách riêng để test nhanh,
tất định 100%, không cần build_daemon."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from apps.paosd.knowledge_store import KnowledgeStore
from apps.paosd.memory_retriever import MemoryRetriever
from apps.paosd.memory_store import MemoryStore
from kernel.events.bus import EventBus
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def events(store: StateStore) -> EventBus:
    return EventBus(store)


@pytest.fixture
def memory_store(store: StateStore, events: EventBus) -> MemoryStore:
    return MemoryStore(store, events)


@pytest.fixture
def knowledge_store(store: StateStore, events: EventBus) -> KnowledgeStore:
    return KnowledgeStore(store, events)


class _FakeRouter:
    """Trả embedding CỐ ĐỊNH bất kể query — retriever không tự đánh giá độ
    giống nhau (đó là việc của `vec_distance_cosine`, đã kiểm qua HTTP thật)."""

    def __init__(self, vector: list[float] = [1.0, 0.0]) -> None:  # noqa: B006 — chỉ đọc, không mutate
        self.vector = vector
        self.calls: list[dict[str, Any]] = []

    async def call(
        self,
        capability_ref: str,
        payload: dict[str, Any],
        process_id: str,
        *,
        exclude_provider: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        self.calls.append({"capability_ref": capability_ref, "payload": payload})
        return {"embedding": self.vector, "dim": len(self.vector), "model": "fake"}, "fake.provider"


@pytest.fixture
def fake_router() -> _FakeRouter:
    return _FakeRouter()


@pytest.fixture
def retriever(
    store: StateStore,
    memory_store: MemoryStore,
    fake_router: _FakeRouter,
    knowledge_store: KnowledgeStore,
) -> MemoryRetriever:
    return MemoryRetriever(store, memory_store, fake_router, knowledge_store)  # type: ignore[arg-type]


async def _write_with_vector(
    memory_store: MemoryStore, content: str, vector: list[float], **kwargs: Any
) -> Any:
    return await memory_store.write(
        tier="L3", content=content, embedding=(vector, "fake"), **kwargs
    )


async def test_search_by_query_returns_vector_hit_above_threshold(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    fake_router.vector = [1.0, 0.0]
    await _write_with_vector(memory_store, "giống hệt query", [1.0, 0.0])

    results = await retriever.search("bất kỳ", tier="L3")
    assert len(results) == 1
    assert results[0].matched_via == "vector"
    assert results[0].score == pytest.approx(1.0)


async def test_search_filters_out_below_cosine_threshold(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    fake_router.vector = [1.0, 0.0]
    # vuông góc -> cosine similarity 0.0 < ngưỡng 0.62 (doc 07 §3)
    await _write_with_vector(memory_store, "không liên quan", [0.0, 1.0])

    results = await retriever.search("bất kỳ", tier="L3")
    assert results == []


async def test_search_calls_text_embed_capability(
    retriever: MemoryRetriever, fake_router: _FakeRouter
) -> None:
    await retriever.search("câu truy vấn", tier="L3")
    assert len(fake_router.calls) == 1
    assert fake_router.calls[0]["capability_ref"] == "text.embed@1"
    assert fake_router.calls[0]["payload"] == {"text": "câu truy vấn"}


async def test_empty_query_skips_vector_search_entirely(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    await memory_store.write(tier="L3", content="x", key="pref.x")
    results = await retriever.search("", tier="L3", key="pref.x")
    assert len(fake_router.calls) == 0
    assert len(results) == 1
    assert results[0].matched_via == "exact_key"


async def test_exact_key_and_vector_hit_do_not_duplicate_same_item(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    fake_router.vector = [1.0, 0.0]
    item = await _write_with_vector(memory_store, "x", [1.0, 0.0], key="pref.x")

    results = await retriever.search("bất kỳ", tier="L3", key="pref.x")
    assert len(results) == 1
    assert results[0].item.memory_id == item.memory_id
    assert results[0].matched_via == "exact_key"  # bước 1 chạy trước, đã seen_ids


async def test_search_applies_recency_boost_to_recently_used_item(
    retriever: MemoryRetriever,
    memory_store: MemoryStore,
    store: StateStore,
    fake_router: _FakeRouter,
) -> None:
    fake_router.vector = [1.0, 0.0]
    recent = await _write_with_vector(memory_store, "vừa dùng", [1.0, 0.0])
    old = await _write_with_vector(memory_store, "lâu rồi không dùng", [1.0, 0.0])

    async def _set_last_used(conn: Any) -> None:
        now = datetime.now(UTC).isoformat()
        long_ago = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        await conn.execute(
            "UPDATE memory_items SET last_used_at = ? WHERE memory_id = ?", (now, recent.memory_id)
        )
        await conn.execute(
            "UPDATE memory_items SET last_used_at = ? WHERE memory_id = ?",
            (long_ago, old.memory_id),
        )

    await store.write(_set_last_used)

    results = await retriever.search("bất kỳ", tier="L3")
    by_id = {r.item.memory_id: r for r in results}
    # cả 2 cùng similarity=1.0, nhưng "recent" được nhân 1.2 -> điểm cao hơn.
    assert by_id[recent.memory_id].score > by_id[old.memory_id].score


async def test_rerank_and_cut_respects_token_budget(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    fake_router.vector = [1.0, 0.0]
    long_content = " ".join(f"tu{i}" for i in range(50))
    await _write_with_vector(memory_store, long_content, [1.0, 0.0])
    await _write_with_vector(memory_store, long_content, [1.0, 0.0])

    results = await retriever.search("bất kỳ", tier="L3", token_budget=60)
    # mỗi item ~50 "token" (word count) -> 2 item sẽ vượt ngân sách 60, chỉ giữ 1.
    assert len(results) == 1


async def test_rerank_and_cut_always_keeps_at_least_one_result(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    """1 item duy nhất vượt ngân sách vẫn phải trả về — không bao giờ trả rỗng
    chỉ vì 1 mục dài hơn ngân sách (tránh mất hết ngữ cảnh)."""
    fake_router.vector = [1.0, 0.0]
    long_content = " ".join(f"tu{i}" for i in range(9999))
    await _write_with_vector(memory_store, long_content, [1.0, 0.0])

    results = await retriever.search("bất kỳ", tier="L3", token_budget=1)
    assert len(results) == 1


async def test_search_touches_returned_items(
    retriever: MemoryRetriever, memory_store: MemoryStore, fake_router: _FakeRouter
) -> None:
    fake_router.vector = [1.0, 0.0]
    item = await _write_with_vector(memory_store, "x", [1.0, 0.0])

    await retriever.search("bất kỳ", tier="L3")
    touched = await memory_store.get(item.memory_id)
    assert touched is not None
    assert touched.use_count == 1
