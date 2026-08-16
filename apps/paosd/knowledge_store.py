"""KnowledgeStore — CRUD cho Knowledge Graph cá nhân (doc 03 §3, doc 07 §4,
P-M5-3). Nhân bản pattern DAO của `apps/paosd/memory_store.py`.

**Khử trùng lặp entity** (doc 07 §4.4): `create_or_reinforce_node()` tra theo
`(type, label)` — khớp không phân biệt hoa/thường trên `label` chính HOẶC
bất kỳ `aliases_json` nào (`sdk/kg_extract.py::label_matches`, quyết định đã
ghi ở đó). Node đã tồn tại được CỦNG CỐ (confidence tăng theo
`sdk.preference.apply_delta(..., SILENT_ACCEPT)`, `last_seen` cập nhật),
không tạo hàng mới — cùng tinh thần `MemoryStore.update()`.

**Mâu thuẫn** (doc 07 §4.4): `create_edge()` GIỮ CẢ HAI khi phát hiện một
cạnh mới mâu thuẫn với cạnh đang hoạt động (`CONTRADICTORY_RELATIONS`,
`sdk/kg_extract.py`) giữa CÙNG cặp (src, dst) — không bao giờ tự invalidate
cạnh cũ, chỉ phát `knowledge.conflict.detected` để con người xét lại sau
(doc 07 §4.4 "hỏi người dùng khi thuận tiện"). `invalidated_at` tồn tại
trong schema (doc 03 §3) cho tương lai (M9 hoặc phiên duyệt tay) nhưng CHƯA
có caller nào tự động set nó ở lát cắt này — không xây cơ chế cho use case
chưa có (P4)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

from kernel import clock, ids
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.redact import redact, redact_deep
from kernel.state.db import StateStore
from sdk.kg_extract import (
    CONTRADICTORY_RELATIONS,
    KG_INITIAL_EDGE_CONFIDENCE,
    KG_INITIAL_NODE_CONFIDENCE,
    RelationType,
    label_matches,
)
from sdk.preference import ObservationKind, apply_delta

_SELECT_NODE = (
    "SELECT node_id, type, label, aliases_json, props_json, confidence, first_seen, last_seen "
    "FROM kg_nodes "
)
_SELECT_EDGE = (
    "SELECT edge_id, src, dst, rel, weight, confidence, provenance_json, created_at, "
    "invalidated_at FROM kg_edges "
)


@dataclass(frozen=True)
class KGNode:
    node_id: str
    type: str
    label: str
    aliases: list[str] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)
    confidence: float = KG_INITIAL_NODE_CONFIDENCE
    first_seen: str = ""
    last_seen: str = ""


@dataclass(frozen=True)
class KGEdge:
    edge_id: str
    src: str
    dst: str
    rel: str
    weight: float = 1.0
    confidence: float = KG_INITIAL_EDGE_CONFIDENCE
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    invalidated_at: str | None = None


def row_to_node(row: Any) -> KGNode:
    return KGNode(
        node_id=row[0],
        type=row[1],
        label=row[2],
        aliases=json.loads(row[3]) if row[3] else [],
        props=json.loads(row[4]) if row[4] else {},
        confidence=row[5],
        first_seen=row[6],
        last_seen=row[7],
    )


def row_to_edge(row: Any) -> KGEdge:
    return KGEdge(
        edge_id=row[0],
        src=row[1],
        dst=row[2],
        rel=row[3],
        weight=row[4],
        confidence=row[5],
        provenance=json.loads(row[6]) if row[6] else {},
        created_at=row[7],
        invalidated_at=row[8],
    )


class KnowledgeStore:
    def __init__(self, store: StateStore, events: EventBus) -> None:
        self._store = store
        self._events = events

    # --- Node -----------------------------------------------------------

    async def find_node(self, type: str, label: str) -> KGNode | None:
        """Quét mọi node CÙNG `type` rồi khớp `label_matches()` phía Python
        — quy mô 1 người dùng (ADR-0011), vài trăm/nghìn node mỗi type là
        đủ nhỏ để brute-force, không cần LOWER()/LIKE ở SQL (giữ logic khớp
        alias tập trung MỘT chỗ, `sdk/kg_extract.py`)."""

        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(_SELECT_NODE + "WHERE type = ?", (type,))
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        for row in rows:
            node = row_to_node(row)
            if label_matches(label, node.label, node.aliases):
                return node
        return None

    async def create_or_reinforce_node(
        self,
        *,
        type: str,
        label: str,
        aliases: list[str] | None = None,
        props: dict[str, Any] | None = None,
        process_id: str | None = None,
    ) -> KGNode:
        existing = await self.find_node(type, label)
        now = clock.now().isoformat()

        if existing is not None:
            new_confidence = apply_delta(existing.confidence, ObservationKind.SILENT_ACCEPT)

            async def _update(conn: aiosqlite.Connection) -> None:
                await conn.execute(
                    "UPDATE kg_nodes SET confidence = ?, last_seen = ? WHERE node_id = ?",
                    (new_confidence, now, existing.node_id),
                )

            await self._store.write(_update)
            return KGNode(
                node_id=existing.node_id,
                type=existing.type,
                label=existing.label,
                aliases=existing.aliases,
                props=existing.props,
                confidence=new_confidence,
                first_seen=existing.first_seen,
                last_seen=now,
            )

        node_id = ids.new_id("kgn")
        clean_label = redact(label)
        clean_aliases = redact_deep(aliases or [])
        clean_props = redact_deep(props or {})

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO kg_nodes(node_id, type, label, aliases_json, props_json, "
                "confidence, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node_id,
                    type,
                    clean_label,
                    json.dumps(clean_aliases, ensure_ascii=False),
                    json.dumps(clean_props, ensure_ascii=False),
                    KG_INITIAL_NODE_CONFIDENCE,
                    now,
                    now,
                ),
            )

        await self._store.write(_insert)
        await self._events.publish(
            EventType.KNOWLEDGE_NODE_CREATED.value,
            source="paosd.knowledge",
            payload={"node_id": node_id, "type": type, "label": clean_label},
            process_id=process_id,
        )
        return KGNode(
            node_id=node_id,
            type=type,
            label=clean_label,
            aliases=clean_aliases,
            props=clean_props,
            confidence=KG_INITIAL_NODE_CONFIDENCE,
            first_seen=now,
            last_seen=now,
        )

    async def get_node(self, node_id: str) -> KGNode | None:
        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(_SELECT_NODE + "WHERE node_id = ?", (node_id,))
            return await cursor.fetchone()

        row = await self._store.read(_select)
        return row_to_node(row) if row is not None else None

    async def list_nodes(self, *, type: str | None = None, limit: int = 100) -> list[KGNode]:
        async def _select(conn: aiosqlite.Connection) -> Any:
            if type is None:
                cursor = await conn.execute(
                    _SELECT_NODE + "ORDER BY last_seen DESC LIMIT ?", (limit,)
                )
            else:
                cursor = await conn.execute(
                    _SELECT_NODE + "WHERE type = ? ORDER BY last_seen DESC LIMIT ?",
                    (type, limit),
                )
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        return [row_to_node(r) for r in rows]

    # --- Edge -------------------------------------------------------------

    async def _find_active_edge(self, src: str, dst: str, rel: str) -> KGEdge | None:
        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(
                _SELECT_EDGE + "WHERE src = ? AND dst = ? AND rel = ? AND invalidated_at IS NULL",
                (src, dst, rel),
            )
            return await cursor.fetchone()

        row = await self._store.read(_select)
        return row_to_edge(row) if row is not None else None

    async def create_edge(
        self,
        *,
        src: str,
        dst: str,
        rel: str,
        provenance: dict[str, Any],
        confidence: float = KG_INITIAL_EDGE_CONFIDENCE,
        weight: float = 1.0,
        process_id: str | None = None,
    ) -> KGEdge:
        existing = await self._find_active_edge(src, dst, rel)
        now = clock.now().isoformat()

        if existing is not None:
            new_confidence = apply_delta(existing.confidence, ObservationKind.SILENT_ACCEPT)
            new_weight = existing.weight + 1.0

            async def _update(conn: aiosqlite.Connection) -> None:
                await conn.execute(
                    "UPDATE kg_edges SET confidence = ?, weight = ? WHERE edge_id = ?",
                    (new_confidence, new_weight, existing.edge_id),
                )

            await self._store.write(_update)
            return KGEdge(
                edge_id=existing.edge_id,
                src=src,
                dst=dst,
                rel=rel,
                weight=new_weight,
                confidence=new_confidence,
                provenance=existing.provenance,
                created_at=existing.created_at,
                invalidated_at=existing.invalidated_at,
            )

        conflicting_rel = (
            CONTRADICTORY_RELATIONS.get(RelationType(rel)) if _is_relation(rel) else None
        )
        conflict_with: KGEdge | None = None
        if conflicting_rel is not None:
            conflict_with = await self._find_active_edge(src, dst, conflicting_rel.value)

        edge_id = ids.new_id("kge")
        clean_provenance = redact_deep(provenance)

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO kg_edges(edge_id, src, dst, rel, weight, confidence, "
                "provenance_json, created_at, invalidated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    edge_id,
                    src,
                    dst,
                    rel,
                    weight,
                    confidence,
                    json.dumps(clean_provenance, ensure_ascii=False),
                    now,
                ),
            )

        await self._store.write(_insert)
        await self._events.publish(
            EventType.KNOWLEDGE_EDGE_CREATED.value,
            source="paosd.knowledge",
            payload={"src": src, "rel": rel, "dst": dst, "confidence": confidence},
            process_id=process_id,
        )
        if conflict_with is not None:
            await self._events.publish(
                EventType.KNOWLEDGE_CONFLICT_DETECTED.value,
                source="paosd.knowledge",
                payload={
                    "node_id": src,
                    "old": f"{conflict_with.rel}:{dst}",
                    "new": f"{rel}:{dst}",
                },
                process_id=process_id,
            )
        return KGEdge(
            edge_id=edge_id,
            src=src,
            dst=dst,
            rel=rel,
            weight=weight,
            confidence=confidence,
            provenance=clean_provenance,
            created_at=now,
            invalidated_at=None,
        )

    async def edges_for_node(self, node_id: str, *, active_only: bool = True) -> list[KGEdge]:
        """Mọi cạnh chạm `node_id`, CẢ 2 chiều (src hoặc dst) — dùng cho
        `walk()` và `GET /v1/knowledge/nodes/{id}` (xem cạnh của 1 node)."""

        async def _select(conn: aiosqlite.Connection) -> Any:
            clause = " AND invalidated_at IS NULL" if active_only else ""
            cursor = await conn.execute(
                _SELECT_EDGE + f"WHERE (src = ? OR dst = ?){clause}",
                (node_id, node_id),
            )
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        return [row_to_edge(r) for r in rows]

    async def walk(self, start: KGNode, *, hops: int = 2) -> list[tuple[int, KGEdge, KGNode]]:
        """Duyệt BFS `hops` bước từ `start` (doc 07 §3 bước 2 — "Knowledge
        Graph walk (2 hop từ entity trong yêu cầu)"). Trả về danh sách
        (số hop — 1 hoặc 2, cạnh, node ở ĐẦU KIA của cạnh đó) — thứ tự: các
        cạnh 1-hop trước, rồi 2-hop, KHÔNG lặp lại cùng 1 cạnh hoặc quay lại
        `start`. Số hop trả kèm để caller (vd `MemoryRetriever`) giảm điểm
        theo khoảng cách."""
        visited_nodes = {start.node_id}
        frontier = [start]
        results: list[tuple[int, KGEdge, KGNode]] = []
        seen_edge_ids: set[str] = set()

        for hop in range(1, hops + 1):
            next_frontier: list[KGNode] = []
            for node in frontier:
                for edge in await self.edges_for_node(node.node_id):
                    if edge.edge_id in seen_edge_ids:
                        continue
                    other_id = edge.dst if edge.src == node.node_id else edge.src
                    if other_id in visited_nodes:
                        seen_edge_ids.add(edge.edge_id)
                        continue
                    other_node = await self.get_node(other_id)
                    if other_node is None:
                        continue
                    seen_edge_ids.add(edge.edge_id)
                    visited_nodes.add(other_id)
                    results.append((hop, edge, other_node))
                    next_frontier.append(other_node)
            frontier = next_frontier
            if not frontier:
                break
        return results

    async def list_edges(self, *, limit: int = 1000) -> list[KGEdge]:
        async def _select(conn: aiosqlite.Connection) -> Any:
            cursor = await conn.execute(_SELECT_EDGE + "ORDER BY created_at DESC LIMIT ?", (limit,))
            return await cursor.fetchall()

        rows = await self._store.read(_select)
        return [row_to_edge(r) for r in rows]

    async def export_jsonld(self) -> dict[str, Any]:
        """doc 07 §4.4 — `knowledge/graph.jsonld`, "đọc được bằng công cụ
        khác, chống lock-in". Không có schema JSON-LD chính thức riêng cho
        Knowledge Graph cá nhân — dựng cấu trúc @context tối giản, TỰ MÔ TẢ
        (mọi field có nghĩa rõ ràng bằng tên tiếng Anh chuẩn), đủ để công cụ
        khác (hoặc chính người dùng) đọc/hiểu mà không cần PAOS."""
        nodes = await self.list_nodes(limit=1_000_000)
        edges = await self.list_edges(limit=1_000_000)
        return {
            "@context": {
                "node_id": "@id",
                "type": "@type",
                "label": "https://schema.org/name",
                "kg_edges": "https://schema.org/about",
            },
            "@graph": [
                {
                    "@id": n.node_id,
                    "@type": n.type,
                    "label": n.label,
                    "aliases": n.aliases,
                    "confidence": n.confidence,
                    "first_seen": n.first_seen,
                    "last_seen": n.last_seen,
                }
                for n in nodes
            ],
            "kg_edges": [
                {
                    "edge_id": e.edge_id,
                    "src": e.src,
                    "dst": e.dst,
                    "rel": e.rel,
                    "weight": e.weight,
                    "confidence": e.confidence,
                    "provenance": e.provenance,
                    "created_at": e.created_at,
                    "invalidated_at": e.invalidated_at,
                }
                for e in edges
            ],
        }


def _is_relation(rel: str) -> bool:
    try:
        RelationType(rel)
    except ValueError:
        return False
    return True
