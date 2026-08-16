"""Kiểm KnowledgeExtractor (doc 07 §4, P-M5-3) — EventEnvelope dựng tay,
artifact chèn thẳng vào DB + file thật trên đĩa (không cần build_daemon đầy
đủ — kịch bản nối dây thật qua Event Bus đã kiểm ở
tests/apps/paosd/test_knowledge_extractor_integration.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.paosd.artifact_store import ArtifactStore
from apps.paosd.knowledge_extractor import KnowledgeExtractor
from apps.paosd.knowledge_store import KnowledgeStore
from kernel.events.bus import EventBus, EventEnvelope
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
def knowledge_store(store: StateStore, events: EventBus) -> KnowledgeStore:
    return KnowledgeStore(store, events)


@pytest.fixture
def artifact_store(store: StateStore, tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(store, tmp_path / "workspace")


@pytest.fixture
def extractor(knowledge_store: KnowledgeStore, artifact_store: ArtifactStore) -> KnowledgeExtractor:
    return KnowledgeExtractor(knowledge_store, artifact_store)


async def _insert_artifact(
    store: StateStore,
    tmp_path: Path,
    *,
    artifact_id: str,
    process_id: str,
    mime: str,
    text: str,
) -> None:
    rel_path = f"artifacts/{artifact_id}/content.txt"
    full_path = tmp_path / "workspace" / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(text, encoding="utf-8")

    async def _insert(conn: Any) -> None:
        await conn.execute(
            "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
            "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
            "VALUES (?, ?, NULL, 'script', ?, ?, 'x', 1, '{}', NULL, NULL, '2026-08-16T00:00:00')",
            (artifact_id, process_id, rel_path, mime),
        )

    await store.write(_insert)


def _script_created(artifact_id: str, seq: int = 0) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{seq}",
        seq=seq,
        type="script.created",
        version=1,
        ts="2026-08-16T00:00:00",
        source="agents.script",
        process_id="proc_1",
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"artifact_id": artifact_id},
    )


async def test_extracts_known_terms_and_links_learned_from_source(
    extractor: KnowledgeExtractor,
    knowledge_store: KnowledgeStore,
    store: StateStore,
    tmp_path: Path,
) -> None:
    await _insert_artifact(
        store,
        tmp_path,
        artifact_id="art_1",
        process_id="proc_1",
        mime="text/plain",
        text="Script này so sánh MongoDB và PostgreSQL cho dự án Database mới.",
    )
    await extractor.on_artifact_event(_script_created("art_1"))

    mongo = await knowledge_store.find_node("Technology", "MongoDB")
    postgres = await knowledge_store.find_node("Technology", "PostgreSQL")
    db = await knowledge_store.find_node("Concept", "Database")
    source = await knowledge_store.find_node("Source", "art_1")
    assert mongo is not None
    assert postgres is not None
    assert db is not None
    assert source is not None

    edges = await knowledge_store.edges_for_node(source.node_id)
    assert len(edges) == 3
    assert all(e.rel == "learned_from" and e.dst == source.node_id for e in edges)
    assert edges[0].provenance["event"] == "script.created"
    assert edges[0].provenance["artifact_id"] == "art_1"


async def test_no_known_terms_creates_nothing(
    extractor: KnowledgeExtractor,
    knowledge_store: KnowledgeStore,
    store: StateStore,
    tmp_path: Path,
) -> None:
    await _insert_artifact(
        store,
        tmp_path,
        artifact_id="art_1",
        process_id="proc_1",
        mime="text/plain",
        text="Hôm nay trời đẹp, không có gì đặc biệt để nói cả.",
    )
    await extractor.on_artifact_event(_script_created("art_1"))
    assert await knowledge_store.list_nodes() == []


async def test_binary_artifact_is_skipped_silently(
    extractor: KnowledgeExtractor,
    knowledge_store: KnowledgeStore,
    store: StateStore,
    tmp_path: Path,
) -> None:
    await _insert_artifact(
        store,
        tmp_path,
        artifact_id="art_1",
        process_id="proc_1",
        mime="image/png",
        text="MongoDB",  # nội dung không quan trọng, mime quyết định
    )
    await extractor.on_artifact_event(_script_created("art_1"))
    assert await knowledge_store.list_nodes() == []


async def test_missing_artifact_id_in_payload_is_ignored(extractor: KnowledgeExtractor) -> None:
    envelope = EventEnvelope(
        event_id="evt_x",
        seq=0,
        type="script.created",
        version=1,
        ts="2026-08-16T00:00:00",
        source="agents.script",
        process_id="proc_1",
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={},
    )
    await extractor.on_artifact_event(envelope)  # không raise


async def test_unknown_artifact_id_is_ignored(extractor: KnowledgeExtractor) -> None:
    await extractor.on_artifact_event(_script_created("art_missing"))  # không raise


async def test_repeated_mention_across_artifacts_reinforces_same_node(
    extractor: KnowledgeExtractor,
    knowledge_store: KnowledgeStore,
    store: StateStore,
    tmp_path: Path,
) -> None:
    await _insert_artifact(
        store, tmp_path, artifact_id="art_1", process_id="proc_1", mime="text/plain", text="MongoDB"
    )
    await _insert_artifact(
        store, tmp_path, artifact_id="art_2", process_id="proc_2", mime="text/plain", text="MongoDB"
    )
    await extractor.on_artifact_event(_script_created("art_1", seq=0))
    await extractor.on_artifact_event(_script_created("art_2", seq=1))

    nodes = await knowledge_store.list_nodes(type="Technology")
    assert len(nodes) == 1
    assert nodes[0].confidence > 0.5  # đã củng cố ít nhất 1 lần
