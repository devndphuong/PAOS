"""Kiểm KnowledgeStore — CRUD Knowledge Graph (doc 03 §3, doc 07 §4, P-M5-3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.paosd.knowledge_store import KnowledgeStore
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.state.db import StateStore
from sdk.kg_extract import KG_INITIAL_NODE_CONFIDENCE, NodeType, RelationType


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
def kg(store: StateStore, events: EventBus) -> KnowledgeStore:
    return KnowledgeStore(store, events)


# --- Node ---------------------------------------------------------------


async def test_create_node_sets_initial_confidence_and_publishes_event(
    kg: KnowledgeStore, events: EventBus
) -> None:
    node = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    assert node.confidence == pytest.approx(KG_INITIAL_NODE_CONFIDENCE)
    assert node.first_seen == node.last_seen

    published = await events.events_since(0, None)
    matches = [e for e in published if e.type == EventType.KNOWLEDGE_NODE_CREATED.value]
    assert len(matches) == 1
    assert matches[0].payload == {"node_id": node.node_id, "type": "Technology", "label": "MongoDB"}


async def test_reinforce_existing_node_case_insensitive_no_duplicate(kg: KnowledgeStore) -> None:
    first = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    second = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="mongodb")

    assert second.node_id == first.node_id
    assert second.confidence == pytest.approx(KG_INITIAL_NODE_CONFIDENCE + 0.10)
    assert len(await kg.list_nodes(type=NodeType.TECHNOLOGY.value)) == 1


async def test_reinforce_existing_node_matches_via_alias(kg: KnowledgeStore) -> None:
    first = await kg.create_or_reinforce_node(
        type=NodeType.TECHNOLOGY.value, label="MongoDB", aliases=["Mongo"]
    )
    second = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="mongo")
    assert second.node_id == first.node_id


async def test_different_type_same_label_creates_separate_nodes(kg: KnowledgeStore) -> None:
    tech = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="Docker")
    tool = await kg.create_or_reinforce_node(type=NodeType.TOOL.value, label="Docker")
    assert tech.node_id != tool.node_id


async def test_get_node_not_found_returns_none(kg: KnowledgeStore) -> None:
    assert await kg.get_node("kgn_missing") is None


async def test_list_nodes_filters_by_type(kg: KnowledgeStore) -> None:
    await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    await kg.create_or_reinforce_node(type=NodeType.TOOL.value, label="Docker")
    tech_only = await kg.list_nodes(type=NodeType.TECHNOLOGY.value)
    assert [n.label for n in tech_only] == ["MongoDB"]


# --- Edge -----------------------------------------------------------------


async def test_create_edge_publishes_event(kg: KnowledgeStore, events: EventBus) -> None:
    a = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    b = await kg.create_or_reinforce_node(type=NodeType.SOURCE.value, label="art_1")
    edge = await kg.create_edge(
        src=a.node_id, dst=b.node_id, rel=RelationType.LEARNED_FROM.value, provenance={"x": 1}
    )
    assert edge.confidence == pytest.approx(0.5)

    published = await events.events_since(0, None)
    matches = [e for e in published if e.type == EventType.KNOWLEDGE_EDGE_CREATED.value]
    assert len(matches) == 1
    assert matches[0].payload == {
        "src": a.node_id,
        "rel": "learned_from",
        "dst": b.node_id,
        "confidence": 0.5,
    }


async def test_create_edge_reinforces_existing_active_edge_no_duplicate(kg: KnowledgeStore) -> None:
    a = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    b = await kg.create_or_reinforce_node(type=NodeType.SOURCE.value, label="art_1")
    e1 = await kg.create_edge(
        src=a.node_id, dst=b.node_id, rel=RelationType.LEARNED_FROM.value, provenance={}
    )
    e2 = await kg.create_edge(
        src=a.node_id, dst=b.node_id, rel=RelationType.LEARNED_FROM.value, provenance={}
    )
    assert e2.edge_id == e1.edge_id
    assert e2.weight == pytest.approx(2.0)
    assert e2.confidence == pytest.approx(0.6)
    assert len(await kg.list_edges()) == 1


async def test_create_edge_detects_conflict_keeps_both_and_emits_event(
    kg: KnowledgeStore, events: EventBus
) -> None:
    person = await kg.create_or_reinforce_node(type=NodeType.PERSON.value, label="user")
    tech = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")

    prefers = await kg.create_edge(
        src=person.node_id, dst=tech.node_id, rel=RelationType.PREFERS.value, provenance={}
    )
    avoids = await kg.create_edge(
        src=person.node_id, dst=tech.node_id, rel=RelationType.AVOIDS.value, provenance={}
    )

    assert prefers.edge_id != avoids.edge_id  # CẢ HAI được giữ, không cái nào bị xoá
    all_edges = await kg.list_edges()
    assert len(all_edges) == 2
    assert all(e.invalidated_at is None for e in all_edges)  # không tự invalidate

    published = await events.events_since(0, None)
    conflicts = [e for e in published if e.type == EventType.KNOWLEDGE_CONFLICT_DETECTED.value]
    assert len(conflicts) == 1
    assert conflicts[0].payload["node_id"] == person.node_id


async def test_non_contradictory_relations_do_not_trigger_conflict(
    kg: KnowledgeStore, events: EventBus
) -> None:
    a = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    b = await kg.create_or_reinforce_node(type=NodeType.CONCEPT.value, label="Database")
    await kg.create_edge(src=a.node_id, dst=b.node_id, rel=RelationType.IS_A.value, provenance={})
    await kg.create_edge(
        src=a.node_id, dst=b.node_id, rel=RelationType.PART_OF.value, provenance={}
    )
    published = await events.events_since(0, None)
    conflicts = [e for e in published if e.type == EventType.KNOWLEDGE_CONFLICT_DETECTED.value]
    assert conflicts == []


# --- walk (BL-013) ---------------------------------------------------------


async def test_walk_finds_one_hop_neighbors(kg: KnowledgeStore) -> None:
    mongo = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    source = await kg.create_or_reinforce_node(type=NodeType.SOURCE.value, label="art_1")
    await kg.create_edge(
        src=mongo.node_id, dst=source.node_id, rel=RelationType.LEARNED_FROM.value, provenance={}
    )

    hits = await kg.walk(mongo, hops=2)
    assert len(hits) == 1
    hop, edge, other = hits[0]
    assert hop == 1
    assert other.node_id == source.node_id
    assert edge.rel == "learned_from"


async def test_walk_finds_two_hop_neighbors_and_marks_hop_distance(kg: KnowledgeStore) -> None:
    mongo = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    db = await kg.create_or_reinforce_node(type=NodeType.CONCEPT.value, label="Database")
    postgres = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="PostgreSQL")
    await kg.create_edge(
        src=mongo.node_id, dst=db.node_id, rel=RelationType.IS_A.value, provenance={}
    )
    await kg.create_edge(
        src=postgres.node_id, dst=db.node_id, rel=RelationType.IS_A.value, provenance={}
    )

    hits = await kg.walk(mongo, hops=2)
    by_node = {other.node_id: (hop, edge) for hop, edge, other in hits}
    assert by_node[db.node_id][0] == 1
    assert by_node[postgres.node_id][0] == 2


async def test_walk_does_not_revisit_start_node(kg: KnowledgeStore) -> None:
    a = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    b = await kg.create_or_reinforce_node(type=NodeType.CONCEPT.value, label="Database")
    await kg.create_edge(src=a.node_id, dst=b.node_id, rel=RelationType.IS_A.value, provenance={})
    await kg.create_edge(
        src=b.node_id, dst=a.node_id, rel=RelationType.SIMILAR_TO.value, provenance={}
    )

    hits = await kg.walk(a, hops=2)
    node_ids_visited = {other.node_id for _, _, other in hits}
    assert a.node_id not in node_ids_visited


async def test_walk_with_no_edges_returns_empty(kg: KnowledgeStore) -> None:
    lone = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="Redis")
    assert await kg.walk(lone, hops=2) == []


# --- export_jsonld ----------------------------------------------------------


async def test_export_jsonld_contains_all_nodes_and_edges(kg: KnowledgeStore) -> None:
    a = await kg.create_or_reinforce_node(type=NodeType.TECHNOLOGY.value, label="MongoDB")
    b = await kg.create_or_reinforce_node(type=NodeType.SOURCE.value, label="art_1")
    await kg.create_edge(
        src=a.node_id, dst=b.node_id, rel=RelationType.LEARNED_FROM.value, provenance={"e": 1}
    )

    graph = await kg.export_jsonld()
    assert "@context" in graph
    assert len(graph["@graph"]) == 2
    assert len(graph["kg_edges"]) == 1
    node_ids = {n["@id"] for n in graph["@graph"]}
    assert node_ids == {a.node_id, b.node_id}
