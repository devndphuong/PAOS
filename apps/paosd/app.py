"""paosd — daemon Kernel + HTTP API cục bộ. Toàn bộ mã HTTP sống ở đây (ADR-0021)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from kernel.errors import PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.process.manager import Process, ProcessManager, ProcessState


class CreateJobRequest(BaseModel):
    intent: str
    spec: dict[str, Any] = {}
    name: str
    workflow_ref: str
    priority: int = 5


class CreateJobResponse(BaseModel):
    process_id: str
    pid: int


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


class ExplainResponse(BaseModel):
    process_id: str
    pid: int
    state: str
    trace: list[EventResponse]


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


def create_app(manager: ProcessManager, events: EventBus) -> FastAPI:
    """Lớp mỏng dịch HTTP <-> Kernel API (doc 04 §1) — không chứa logic nghiệp vụ."""
    app = FastAPI(title="paosd")

    @app.post("/v1/jobs", response_model=CreateJobResponse)
    async def create_job(body: CreateJobRequest) -> CreateJobResponse:
        try:
            process = await manager.create(
                intent=body.intent,
                spec=body.spec,
                name=body.name,
                workflow_ref=body.workflow_ref,
                priority=body.priority,
            )
        except PaosError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
        return CreateJobResponse(process_id=process.process_id, pid=process.pid)

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
        return ExplainResponse(
            process_id=process.process_id,
            pid=process.pid,
            state=process.state.value,
            trace=[_to_event_response(e) for e in trace],
        )

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

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
