"""Kiểm CacheStore — cache theo nội dung (content-addressed), doc 03 §5.2, doc 19 P-M2-4."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from apps.paosd.cache_store import CacheStore
from kernel.state.db import StateStore

_KEY_FIELDS = ["prompt", "system"]


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def cache(store: StateStore, tmp_path: Path) -> CacheStore:
    return CacheStore(store, tmp_path / "cache")


def test_compute_key_deterministic_for_same_input() -> None:
    payload = {"prompt": "xin chào", "system": "vui vẻ", "task_class": "x"}
    k1 = CacheStore.compute_key("text.generate", 1, payload, _KEY_FIELDS, "local")
    k2 = CacheStore.compute_key("text.generate", 1, payload, _KEY_FIELDS, "local")
    assert k1 == k2


def test_compute_key_ignores_fields_outside_allowlist() -> None:
    # task_class không nằm trong _KEY_FIELDS — đổi giá trị nó không được đổi key
    # (doc 04 §2.1: cache_key_fields là allowlist, không hash nguyên payload).
    base = {"prompt": "x", "system": "y", "task_class": "a"}
    other = {"prompt": "x", "system": "y", "task_class": "b"}
    assert CacheStore.compute_key(
        "text.generate", 1, base, _KEY_FIELDS, "local"
    ) == CacheStore.compute_key("text.generate", 1, other, _KEY_FIELDS, "local")


def test_compute_key_differs_by_provider_class() -> None:
    payload = {"prompt": "x", "system": "y"}
    local_key = CacheStore.compute_key("text.generate", 1, payload, _KEY_FIELDS, "local")
    cloud_key = CacheStore.compute_key("text.generate", 1, payload, _KEY_FIELDS, "cloud")
    assert local_key != cloud_key


def test_compute_key_differs_by_key_field_value() -> None:
    k1 = CacheStore.compute_key("text.generate", 1, {"prompt": "a"}, _KEY_FIELDS, "local")
    k2 = CacheStore.compute_key("text.generate", 1, {"prompt": "b"}, _KEY_FIELDS, "local")
    assert k1 != k2


async def test_lookup_misses_when_never_stored(cache: CacheStore) -> None:
    assert await cache.lookup("khong-ton-tai") is None


async def test_store_then_lookup_hits_and_increments_hit_count(
    cache: CacheStore, store: StateStore
) -> None:
    key = CacheStore.compute_key("text.generate", 1, {"prompt": "x"}, _KEY_FIELDS, "local")
    await cache.store(key, "text.generate", 1, "local", "stub.deterministic", {"text": "ok"})

    hit = await cache.lookup(key)
    assert hit is not None
    assert hit.result == {"text": "ok"}
    assert hit.provider_id == "stub.deterministic"
    assert hit.provider_class == "local"

    async def _read_hit_count(conn: aiosqlite.Connection) -> int:
        cursor = await conn.execute(
            "SELECT hit_count FROM cache_entries WHERE cache_key = ?", (key,)
        )
        row = await cursor.fetchone()
        assert row is not None
        return int(row[0])

    await cache.lookup(key)  # lần trúng thứ 2
    assert await store.read(_read_hit_count) == 2


async def test_lookup_gracefully_misses_when_file_deleted(
    cache: CacheStore, tmp_path: Path
) -> None:
    """Doc 03 dòng 293: xoá cache/ bất cứ lúc nào không được làm sai kết quả, chỉ
    làm chậm — record DB còn nhưng file mất phải coi là cache miss, không raise."""
    key = CacheStore.compute_key("text.generate", 1, {"prompt": "x"}, _KEY_FIELDS, "local")
    await cache.store(key, "text.generate", 1, "local", "stub.deterministic", {"text": "ok"})

    for f in (tmp_path / "cache").rglob("*.json"):
        f.unlink()

    assert await cache.lookup(key) is None  # KHÔNG raise


async def test_concurrent_store_with_same_key_does_not_raise(cache: CacheStore) -> None:
    """Content-addressed: trùng key = trùng nội dung — 2 Process song song (M1-3a)
    cùng miss cache cho cùng nội dung không được vỡ UNIQUE constraint."""
    key = CacheStore.compute_key("text.generate", 1, {"prompt": "x"}, _KEY_FIELDS, "local")
    await asyncio.gather(
        cache.store(key, "text.generate", 1, "local", "stub.deterministic", {"text": "ok"}),
        cache.store(key, "text.generate", 1, "local", "stub.deterministic", {"text": "ok"}),
    )
    hit = await cache.lookup(key)
    assert hit is not None
    assert hit.result == {"text": "ok"}
