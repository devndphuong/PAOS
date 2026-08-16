"""paosd — daemon Kernel + HTTP API cục bộ. Toàn bộ mã HTTP sống ở đây (ADR-0021)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from apps.paosd.artifact_store import ArtifactNotEditable, ArtifactNotFound, ArtifactStore
from apps.paosd.decision_engine import DecisionEngine
from apps.paosd.knowledge_store import KGEdge, KGNode, KnowledgeStore
from apps.paosd.memory_retriever import MemoryRetriever, RetrievedMemory
from apps.paosd.memory_store import MemoryItem, MemoryStore
from apps.paosd.runner import Runner
from kernel.errors import PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.process.manager import Process, ProcessManager, ProcessState
from kernel.state.db import StateStore
from sdk.preference import decide_promotion


class CreateJobRequest(BaseModel):
    intent: str
    spec: dict[str, Any] = {}
    name: str
    # Optional từ P-M6-1 (doc 19): thiếu -> DecisionEngine tự chọn theo `intent`
    # (policies/intents.yaml). Truyền thẳng vẫn hợp lệ — bỏ qua Decision Engine
    # hoàn toàn, đúng đường cũ M0-M5.
    workflow_ref: str | None = None
    priority: int = 5


class CreateJobResponse(BaseModel):
    process_id: str
    pid: int
    workflow_ref: str


class ReplayRequest(BaseModel):
    from_ts: str
    to_ts: str
    to_subscriber: str


class ReplayResponse(BaseModel):
    replayed: int


class ProcessResponse(BaseModel):
    process_id: str
    pid: int
    job_id: str
    name: str
    workflow_ref: str
    state: str
    progress: float
    started_at: str | None
    ended_at: str | None
    error_code: str | None


class EventResponse(BaseModel):
    event_id: str
    seq: int
    type: str
    version: int
    ts: str
    source: str
    process_id: str | None
    task_id: str | None
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]


class DeadLetterResponse(BaseModel):
    event_id: str
    subscriber: str
    attempts: int
    last_error: str | None
    type: str
    ts: str
    process_id: str | None


class DecisionResponse(BaseModel):
    decision_id: str
    scope: str
    question: str
    candidates: list[dict[str, Any]]
    chosen: str | None
    rationale: str
    policy_version: str | None
    created_at: str


class ExplainResponse(BaseModel):
    process_id: str
    pid: int
    state: str
    trace: list[EventResponse]
    decisions: list[DecisionResponse]


class ArtifactResponse(BaseModel):
    artifact_id: str
    process_id: str
    type: str
    mime: str
    path: str
    content: str
    created_at: str


class ArtifactEditRequest(BaseModel):
    text: str


class ArtifactEditResponse(BaseModel):
    edited_artifact_id: str
    edit_rate: float


class MemoryItemResponse(BaseModel):
    memory_id: str
    tier: str
    scope_id: str | None
    kind: str | None
    key: str | None
    content: str
    confidence: float
    salience: float
    created_at: str
    last_used_at: str | None
    promotion: str
    score: float | None = None
    matched_via: str | None = None


class KGNodeResponse(BaseModel):
    node_id: str
    type: str
    label: str
    aliases: list[str]
    confidence: float
    first_seen: str
    last_seen: str


class KGEdgeResponse(BaseModel):
    edge_id: str
    src: str
    dst: str
    rel: str
    weight: float
    confidence: float
    provenance: dict[str, Any]
    created_at: str
    invalidated_at: str | None


class KGNodeDetailResponse(BaseModel):
    node: KGNodeResponse
    edges: list[KGEdgeResponse]


class KGExportResponse(BaseModel):
    path: str
    node_count: int
    edge_count: int


class MemoryForgetResponse(BaseModel):
    memory_id: str
    forgotten: bool


class MemoryExportResponse(BaseModel):
    path: str
    count: int


class MemoryImportRequest(BaseModel):
    items: list[dict[str, Any]]


class MemoryImportResponse(BaseModel):
    count: int


def _to_event_response(e: EventEnvelope) -> EventResponse:
    return EventResponse(
        event_id=e.event_id,
        seq=e.seq,
        type=e.type,
        version=e.version,
        ts=e.ts,
        source=e.source,
        process_id=e.process_id,
        task_id=e.task_id,
        correlation_id=e.correlation_id,
        causation_id=e.causation_id,
        payload=e.payload,
    )


async def _decisions_for_process(store: StateStore, process_id: str) -> list[DecisionResponse]:
    """doc 06 §1.1/§2.1 Decision Record — nguồn cho `paosctl explain --decisions`
    (P-M6-3). `candidates_json` NULL ở scope `cache_hit` (Router không xét ứng
    viên nào khi trúng cache, xem `Router._write_cache_hit_decision()`)."""

    async def _select(conn: aiosqlite.Connection) -> list[tuple[Any, ...]]:
        cursor = await conn.execute(
            "SELECT decision_id, scope, question, candidates_json, chosen, rationale, "
            "policy_version, created_at FROM decisions WHERE process_id = ? ORDER BY rowid",
            (process_id,),
        )
        return await cursor.fetchall()

    rows = await store.read(_select)
    return [
        DecisionResponse(
            decision_id=row[0],
            scope=row[1],
            question=row[2],
            candidates=json.loads(row[3]) if row[3] else [],
            chosen=row[4],
            rationale=row[5],
            policy_version=row[6],
            created_at=row[7],
        )
        for row in rows
    ]


def _to_response(p: Process) -> ProcessResponse:
    return ProcessResponse(
        process_id=p.process_id,
        pid=p.pid,
        job_id=p.job_id,
        name=p.name,
        workflow_ref=p.workflow_ref,
        state=p.state.value,
        progress=p.progress,
        started_at=p.started_at,
        ended_at=p.ended_at,
        error_code=p.error_code,
    )


def _memory_item_response(
    item: MemoryItem, *, score: float | None, matched_via: str | None
) -> MemoryItemResponse:
    return MemoryItemResponse(
        memory_id=item.memory_id,
        tier=item.tier,
        scope_id=item.scope_id,
        kind=item.kind,
        key=item.key,
        content=item.content,
        confidence=item.confidence,
        salience=item.salience,
        created_at=item.created_at,
        last_used_at=item.last_used_at,
        promotion=decide_promotion(item.confidence).value,
        score=score,
        matched_via=matched_via,
    )


def _kg_node_response(n: KGNode) -> KGNodeResponse:
    return KGNodeResponse(
        node_id=n.node_id,
        type=n.type,
        label=n.label,
        aliases=n.aliases,
        confidence=n.confidence,
        first_seen=n.first_seen,
        last_seen=n.last_seen,
    )


def _kg_edge_response(e: KGEdge) -> KGEdgeResponse:
    return KGEdgeResponse(
        edge_id=e.edge_id,
        src=e.src,
        dst=e.dst,
        rel=e.rel,
        weight=e.weight,
        confidence=e.confidence,
        provenance=e.provenance,
        created_at=e.created_at,
        invalidated_at=e.invalidated_at,
    )


def create_app(  # noqa: PLR0915 — đăng ký route tăng tuyến tính theo số endpoint, không phải độ phức tạp
    manager: ProcessManager,
    events: EventBus,
    runner: Runner,
    store: StateStore,
    workspace_root: Path,
    decision_engine: DecisionEngine,
) -> FastAPI:
    """Lớp mỏng dịch HTTP <-> Kernel API (doc 04 §1) — không chứa logic nghiệp vụ.
    `store`/`workspace_root` chỉ dùng để dựng `ArtifactStore` (P-M4-3) — cùng
    tiền lệ `Router` tự dựng `CacheStore` nội bộ (`apps/paosd/router.py`),
    không cần một tầng "wiring" riêng cho 1 DAO nhỏ. `MemoryRetriever` (P-M5-1)
    tái dùng `runner.router` — KHÔNG dựng Router thứ 2 song song. `KnowledgeStore`
    (P-M5-3) cùng vai trò — dựng tại chỗ, không qua wiring riêng. `decision_engine`
    (P-M6-1) KHÁC — nhận từ ngoài (`wiring.py::build_daemon()`), không dựng tại
    chỗ, vì cùng 1 instance còn phải subscribe `record_outcome()` cho
    kernel.process.completed/failed — dựng 2 lần sẽ tách rời instance đang chấm
    điểm khỏi instance đang học từ outcome."""
    app = FastAPI(title="paosd")
    artifacts = ArtifactStore(store, workspace_root)
    memory_store = MemoryStore(store, events)
    knowledge_store = KnowledgeStore(store, events)
    memory_retriever = MemoryRetriever(store, memory_store, runner.router, knowledge_store)

    @app.post("/v1/jobs", response_model=CreateJobResponse)
    async def create_job(body: CreateJobRequest) -> CreateJobResponse:
        try:
            workflow_ref = body.workflow_ref
            selection = None
            if workflow_ref is None:
                # Thiếu workflow_ref -> Decision Engine chọn theo intent (doc 06
                # §1.1 bước 1-3). Chấm điểm TRƯỚC khi process_id tồn tại — ghi
                # Decision Record (bước 5) chỉ sau khi manager.create() xong,
                # xem docstring apps/paosd/decision_engine.py.
                selection = await decision_engine.choose_workflow(
                    intent=body.intent, spec=body.spec
                )
                workflow_ref = selection.chosen.ref
            process = await manager.create(
                intent=body.intent,
                spec=body.spec,
                name=body.name,
                workflow_ref=workflow_ref,
                priority=body.priority,
            )
            if selection is not None:
                await decision_engine.record_selection(process.process_id, selection)
        except PaosError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        return CreateJobResponse(
            process_id=process.process_id, pid=process.pid, workflow_ref=process.workflow_ref
        )

    @app.get("/v1/processes", response_model=list[ProcessResponse])
    async def list_processes(state: str | None = None) -> list[ProcessResponse]:
        parsed_state: ProcessState | None = None
        if state is not None:
            try:
                parsed_state = ProcessState(state)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"state không hợp lệ: {state}") from exc
        processes = await manager.list(state=parsed_state)
        return [_to_response(p) for p in processes]

    @app.get("/v1/processes/{pid}", response_model=ProcessResponse)
    async def get_process(pid: int) -> ProcessResponse:
        process = await manager.get_by_pid(pid)
        if process is None:
            raise HTTPException(status_code=404, detail=f"Process pid={pid} không tồn tại")
        return _to_response(process)

    @app.get("/v1/processes/{pid}/explain", response_model=ExplainResponse)
    async def explain(pid: int) -> ExplainResponse:
        process = await manager.get_by_pid(pid)
        if process is None:
            raise HTTPException(status_code=404, detail=f"Process pid={pid} không tồn tại")
        trace = await events.events_for_process(process.process_id)
        decisions = await _decisions_for_process(store, process.process_id)
        return ExplainResponse(
            process_id=process.process_id,
            pid=process.pid,
            state=process.state.value,
            trace=[_to_event_response(e) for e in trace],
            decisions=decisions,
        )

    @app.post("/v1/processes/{pid}/cancel", response_model=ProcessResponse)
    async def cancel_process(pid: int) -> ProcessResponse:
        process = await manager.get_by_pid(pid)
        if process is None:
            raise HTTPException(status_code=404, detail=f"Process pid={pid} không tồn tại")
        try:
            updated = await runner.cancel(process.process_id)
        except PaosError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        return _to_response(updated)

    @app.get("/v1/events", response_model=list[EventResponse])
    async def list_events(pid: int | None = None, since_seq: int = 0) -> list[EventResponse]:
        process_id: str | None = None
        if pid is not None:
            process = await manager.get_by_pid(pid)
            if process is None:
                raise HTTPException(status_code=404, detail=f"Process pid={pid} không tồn tại")
            process_id = process.process_id
        trace = await events.events_since(since_seq, process_id)
        return [_to_event_response(e) for e in trace]

    @app.get("/v1/events/dead-letters", response_model=list[DeadLetterResponse])
    async def dead_letters() -> list[DeadLetterResponse]:
        rows = await events.dead_letters()
        return [DeadLetterResponse(**row) for row in rows]

    @app.post("/v1/events/replay", response_model=ReplayResponse)
    async def replay_events(body: ReplayRequest) -> ReplayResponse:
        try:
            count = await events.replay(body.from_ts, body.to_ts, body.to_subscriber)
        except PaosError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        return ReplayResponse(replayed=count)

    @app.get("/v1/artifacts/{artifact_id}", response_model=ArtifactResponse)
    async def get_artifact(artifact_id: str) -> ArtifactResponse:
        record = await artifacts.get(artifact_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' không tồn tại")
        try:
            content = await artifacts.read_content(record)
        except ArtifactNotEditable as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        return ArtifactResponse(
            artifact_id=record.artifact_id,
            process_id=record.process_id,
            type=record.type,
            mime=record.mime,
            path=record.path,
            content=content,
            created_at=record.created_at,
        )

    @app.post("/v1/artifacts/{artifact_id}/edited", response_model=ArtifactEditResponse)
    async def edit_artifact(artifact_id: str, body: ArtifactEditRequest) -> ArtifactEditResponse:
        """doc 08 §5, doc 13 M4 exit criteria — ghi `edit_rate` khi người dùng
        tự tay sửa 1 artifact. Đây là tín hiệu KHÁCH QUAN, không phải điểm
        rubric — apps/paosd/artifact_store.py::record_edit() làm toàn bộ việc
        đo lường + ghi bản artifact mới (supersedes)."""
        try:
            result = await artifacts.record_edit(artifact_id, body.text)
        except ArtifactNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ArtifactNotEditable as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        record = await artifacts.get(artifact_id)
        await events.publish(
            EventType.ARTIFACT_EDITED.value,
            source="paosd",
            payload={
                "artifact_id": artifact_id,
                "edited_artifact_id": result.edited_artifact_id,
                "edit_rate": round(result.edit_rate, 4),
            },
            process_id=record.process_id if record else None,
        )
        return ArtifactEditResponse(
            edited_artifact_id=result.edited_artifact_id, edit_rate=result.edit_rate
        )

    @app.get("/v1/memory", response_model=list[MemoryItemResponse])
    async def query_memory(
        tier: str | None = None, q: str | None = None, key: str | None = None, limit: int = 20
    ) -> list[MemoryItemResponse]:
        """doc 04 §1 — truy vấn memory. `q` có → chạy truy hồi lai thật (doc 07
        §3, cần gọi `text.embed@1`); không có `q` → chỉ liệt kê theo `tier`
        (duyệt thô, không cần embed, đúng vai trò `paosctl memory list`)."""
        if q:
            results: list[RetrievedMemory] = await memory_retriever.search(q, tier=tier, key=key)
            return [
                _memory_item_response(r.item, score=r.score, matched_via=r.matched_via)
                for r in results
            ]
        if tier is None:
            raise HTTPException(status_code=400, detail="Cần ít nhất 'tier' hoặc 'q'")
        items = await memory_store.list_by_tier(tier, limit=limit)
        return [_memory_item_response(i, score=None, matched_via=None) for i in items]

    @app.get("/v1/memory/{memory_id}", response_model=MemoryItemResponse)
    async def get_memory_item(memory_id: str) -> MemoryItemResponse:
        """doc 07 §6 — `paosctl memory show <id>`."""
        item = await memory_store.get(memory_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' không tồn tại")
        return _memory_item_response(item, score=None, matched_via=None)

    @app.delete("/v1/memory/{memory_id}", response_model=MemoryForgetResponse)
    async def forget_memory(memory_id: str) -> MemoryForgetResponse:
        """doc 07 §6, ADR-0029 — `paosctl memory forget <id>`. XÓA CỨNG THẬT,
        không qua Trash (ngoại lệ có chủ đích với ADR-0012, xem ADR-0029)."""
        forgotten = await memory_store.forget(memory_id)
        if not forgotten:
            raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' không tồn tại")
        return MemoryForgetResponse(memory_id=memory_id, forgotten=True)

    @app.post("/v1/memory/export", response_model=MemoryExportResponse)
    async def export_memory() -> MemoryExportResponse:
        """doc 07 §6 — "Xuất toàn bộ memory dạng JSON để bạn tự soi và tự
        sửa". THỦ CÔNG, cùng tiền lệ `/v1/knowledge/export` (P-M5-3)."""
        items = await memory_store.export_json()
        out_path = workspace_root / "memory" / "export.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        return MemoryExportResponse(
            path=str(out_path.relative_to(workspace_root)), count=len(items)
        )

    @app.post("/v1/memory/import", response_model=MemoryImportResponse)
    async def import_memory(body: MemoryImportRequest) -> MemoryImportResponse:
        """doc 07 §6 — nhập lại JSON đã xuất (và có thể đã tự sửa tay).
        Client (`paosctl memory import <file>`) đọc file cục bộ rồi gửi mảng
        item qua đây — server không tự đọc file theo đường dẫn (tránh path
        traversal, nhất quán `AgentContext.write_artifact` đã có)."""
        count = await memory_store.import_json(body.items)
        return MemoryImportResponse(count=count)

    @app.get("/v1/knowledge/nodes", response_model=list[KGNodeResponse])
    async def list_kg_nodes(type: str | None = None, limit: int = 100) -> list[KGNodeResponse]:
        """doc 07 §4 — duyệt Knowledge Graph. `paosctl knowledge list`."""
        nodes = await knowledge_store.list_nodes(type=type, limit=limit)
        return [_kg_node_response(n) for n in nodes]

    @app.get("/v1/knowledge/nodes/{node_id}", response_model=KGNodeDetailResponse)
    async def get_kg_node(node_id: str) -> KGNodeDetailResponse:
        """1 node kèm mọi cạnh chạm nó (cả 2 chiều) — `paosctl knowledge show`,
        trả lời "vì sao PAOS biết cái này" (doc 07 §4.4, provenance)."""
        node = await knowledge_store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node '{node_id}' không tồn tại")
        edges = await knowledge_store.edges_for_node(node_id)
        return KGNodeDetailResponse(
            node=_kg_node_response(node), edges=[_kg_edge_response(e) for e in edges]
        )

    @app.post("/v1/knowledge/export", response_model=KGExportResponse)
    async def export_kg() -> KGExportResponse:
        """doc 07 §4.4 — xuất `knowledge/graph.jsonld`. THỦ CÔNG (doc 18 §8:
        "chạy thủ công thay vì lịch tự động" — không có scheduler infra tới
        trước M7), gọi qua `paosctl knowledge export`."""
        graph = await knowledge_store.export_jsonld()
        out_path = workspace_root / "knowledge" / "graph.jsonld"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        return KGExportResponse(
            path=str(out_path.relative_to(workspace_root)),
            node_count=len(graph["@graph"]),
            edge_count=len(graph["kg_edges"]),
        )

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
