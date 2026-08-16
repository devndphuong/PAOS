"""Kiểm sdk/kg_extract.py — hàm thuần, không I/O (doc 07 §4, P-M5-3)."""

from __future__ import annotations

from sdk.kg_extract import (
    CONTRADICTORY_RELATIONS,
    NodeType,
    RelationType,
    extract_entities,
    label_matches,
)


def test_extract_entities_finds_known_terms_case_insensitive() -> None:
    entities = extract_entities("mình đang học mongodb và postgresql cho video mới")
    labels = {e.label for e in entities}
    assert labels == {"MongoDB", "PostgreSQL"}


def test_extract_entities_assigns_correct_node_type() -> None:
    entities = extract_entities("dùng Docker chạy Ollama, cả hai đều là Tool")
    by_label = {e.label: e.type for e in entities}
    assert by_label["Docker"] == NodeType.TOOL
    assert by_label["Ollama"] == NodeType.TOOL


def test_extract_entities_deduplicates_repeated_mentions() -> None:
    entities = extract_entities("MongoDB rất tốt. MongoDB nhanh. mongodb dễ dùng.")
    assert len(entities) == 1
    assert entities[0].label == "MongoDB"


def test_extract_entities_ignores_unknown_words() -> None:
    entities = extract_entities("Hôm nay trời đẹp, tôi thích uống cà phê")
    assert entities == []


def test_extract_entities_is_pure_and_reproducible() -> None:
    """doc 13 M5 exit criterion "rebuild KG từ replay cho kết quả tương
    đương" — cùng input phải luôn cho CÙNG output tuyệt đối."""
    text = "So sánh MongoDB, PostgreSQL và Redis cho dự án Database mới"
    first = extract_entities(text)
    second = extract_entities(text)
    assert first == second


def test_extract_entities_does_not_match_substring_inside_longer_word() -> None:
    """Token hoá theo ranh giới ký tự chữ/số — "APIs" không nên khớp "API"
    nếu không đúng token đầy đủ... thực ra "APIs" tách thành 1 token "APIs"
    KHÔNG khớp "API" (so khớp CHÍNH XÁC theo token, không phải substring)."""
    entities = extract_entities("chúng ta có nhiều APIs")
    assert entities == []


def test_label_matches_exact_case_insensitive() -> None:
    assert label_matches("mongodb", "MongoDB", [])
    assert label_matches("MONGODB", "MongoDB", [])
    assert not label_matches("postgres", "MongoDB", [])


def test_label_matches_checks_aliases() -> None:
    assert label_matches("mongo", "MongoDB", ["Mongo", "mongo db"])
    assert not label_matches("mysql", "MongoDB", ["Mongo"])


def test_contradictory_relations_prefers_avoids_are_symmetric() -> None:
    assert CONTRADICTORY_RELATIONS[RelationType.PREFERS] == RelationType.AVOIDS
    assert CONTRADICTORY_RELATIONS[RelationType.AVOIDS] == RelationType.PREFERS


def test_node_type_and_relation_type_match_doc07_vocabulary() -> None:
    """doc 07 §4.1/§4.2 — 12 loại node, 13 loại quan hệ, nguyên văn."""
    assert {t.value for t in NodeType} == {
        "Concept",
        "Technology",
        "Tool",
        "Project",
        "Artifact",
        "Person",
        "Preference",
        "Workflow",
        "Provider",
        "Error",
        "Template",
        "Source",
    }
    assert {r.value for r in RelationType} == {
        "is_a",
        "part_of",
        "used_in",
        "depends_on",
        "alternative_to",
        "replaced_by",
        "prefers",
        "avoids",
        "causes",
        "fixes",
        "produced_by",
        "learned_from",
        "similar_to",
    }
