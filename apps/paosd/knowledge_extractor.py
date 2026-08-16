"""KnowledgeExtractor — quan sát Event, trích entity vào Knowledge Graph
(doc 07 §4, doc 07 §5.2, P-M5-3).

Subscriber EventBus (đăng ký ở `apps/paosd/wiring.py`, TRƯỚC `events.start()`
— cùng vị trí `MemoryWriter`, cần cho REL-01 catch-up khi restart). Chỉ quan
sát 3 event THẬT đã tồn tại, đúng discipline `memory_writer.py` đã lập:
KHÔNG bịa event `agent.*.completed` (không ai publish wildcard đó) — dùng
3 event tạo-artifact CỤ THỂ đã có caller thật (`agents/planning/`,
`agents/script/`, `agents/summarize/`, xem `kernel/events/types.py`):

  `plan.created` / `script.created` / `summary.created` — payload chỉ có
  `artifact_id` (schemas/events/*.v1.schema.json) → phải tự đọc nội dung
  qua `ArtifactStore.get()` + `.read_content()` mới có văn bản để trích.

Artifact không phải text (`ArtifactNotEditable`, vd ảnh/audio) bị bỏ qua
lặng lẽ — không phải lỗi, chỉ là "không có gì để trích" (đối xứng với cách
`memory_writer.py` bỏ qua spec key ngoài allowlist).

Mỗi entity trích được tạo/củng cố 1 node (`KnowledgeStore.create_or_reinforce_node`,
khử trùng lặp theo `sdk/kg_extract.py::label_matches`) và MỘT cạnh
`learned_from` trỏ tới node `Source` đại diện cho chính artifact đã sinh ra
observation này (doc 07 §4.3 nguyên văn ví dụ "(MongoDB) -[learned_from]->
(Source: mongodb.pdf)") — KHÔNG suy luận quan hệ nào khác giữa các entity
cùng artifact (xem giới hạn ghi ở `sdk/kg_extract.py` docstring)."""

from __future__ import annotations

from apps.paosd.artifact_store import ArtifactNotEditable, ArtifactStore
from apps.paosd.knowledge_store import KnowledgeStore
from kernel.events.bus import EventEnvelope
from sdk.kg_extract import NodeType, RelationType, extract_entities


class KnowledgeExtractor:
    def __init__(self, knowledge_store: KnowledgeStore, artifact_store: ArtifactStore) -> None:
        self._knowledge_store = knowledge_store
        self._artifact_store = artifact_store

    async def on_artifact_event(self, envelope: EventEnvelope) -> None:
        artifact_id = envelope.payload.get("artifact_id")
        if not isinstance(artifact_id, str):
            return
        record = await self._artifact_store.get(artifact_id)
        if record is None:
            return
        try:
            content = await self._artifact_store.read_content(record)
        except ArtifactNotEditable:
            return

        entities = extract_entities(content)
        if not entities:
            return

        source_node = await self._knowledge_store.create_or_reinforce_node(
            type=NodeType.SOURCE.value,
            label=artifact_id,
            aliases=[record.path],
            process_id=record.process_id,
        )
        for entity in entities:
            node = await self._knowledge_store.create_or_reinforce_node(
                type=entity.type.value,
                label=entity.label,
                process_id=record.process_id,
            )
            await self._knowledge_store.create_edge(
                src=node.node_id,
                dst=source_node.node_id,
                rel=RelationType.LEARNED_FROM.value,
                provenance={
                    "event": envelope.type,
                    "artifact_id": artifact_id,
                    "process_id": record.process_id,
                },
                process_id=record.process_id,
            )
