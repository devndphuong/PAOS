"""Kiểm KnowledgeExtractor nối dây THẬT qua `build_daemon()` (P-M5-3) — khác
tests/apps/paosd/test_knowledge_extractor.py (đơn vị, EventEnvelope dựng
tay): ở đây chạy thật SummarizeAgent (`agent:summarize.agent@1`), `EventBus`
thật phát `summary.created` thật, KnowledgeExtractor đăng ký TRƯỚC
`events.start()` (`apps/paosd/wiring.py`) phải tự bắt được — đúng con đường
production, không tắt qua constructor. Cùng khuôn mẫu
tests/apps/paosd/test_memory_writer_integration.py và tests/apps/paosd/test_runner.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


async def _wait_for_terminal(
    client: httpx.AsyncClient, pid: int, max_wait_s: float = 5.0
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    body: dict[str, Any] = {}
    while loop.time() < deadline:
        body = (await client.get(f"/v1/processes/{pid}")).json()
        if body["state"] in _TERMINAL_STATES:
            return body
        await asyncio.sleep(0.01)
    return body


async def test_real_summarize_job_extracts_known_entities_into_kg(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "summarize",
                    "spec": {"text": "So sánh MongoDB và PostgreSQL cho dự án mới"},
                    "name": "kg-test",
                    "workflow_ref": "agent:summarize.agent@1",
                },
            )
            assert resp.status_code == 200
            pid = resp.json()["pid"]
            status = await _wait_for_terminal(client, pid)
            assert status["state"] == "SUCCEEDED"

            nodes = (await client.get("/v1/knowledge/nodes", params={"type": "Technology"})).json()
            labels = {n["label"] for n in nodes}
            assert "MongoDB" in labels
            assert "PostgreSQL" in labels

            mongo_node = next(n for n in nodes if n["label"] == "MongoDB")
            detail = (await client.get(f"/v1/knowledge/nodes/{mongo_node['node_id']}")).json()
            assert len(detail["edges"]) == 1
            assert detail["edges"][0]["rel"] == "learned_from"
            source_id = detail["edges"][0]["dst"]

            source_nodes = (
                await client.get("/v1/knowledge/nodes", params={"type": "Source"})
            ).json()
            assert any(n["node_id"] == source_id for n in source_nodes)
    finally:
        await daemon.stop()


async def test_knowledge_export_writes_graph_jsonld(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "summarize",
                    "spec": {"text": "Ghi chú về Docker"},
                    "name": "kg-export-test",
                    "workflow_ref": "agent:summarize.agent@1",
                },
            )
            pid = resp.json()["pid"]
            await _wait_for_terminal(client, pid)

            export_resp = await client.post("/v1/knowledge/export")
            assert export_resp.status_code == 200
            body = export_resp.json()
            assert body["node_count"] >= 2  # Docker + Source
            assert body["edge_count"] >= 1

            out_file = tmp_path / "workspace" / "knowledge" / "graph.jsonld"
            assert out_file.exists()
    finally:
        await daemon.stop()


async def test_memory_search_uses_kg_walk_for_related_entity_in_query(tmp_path: Path) -> None:
    """doc 07 §3 bước 2 (BL-013) — sau khi PAOS đã học node "MongoDB" (từ 1
    job trước, gắn `learned_from` tới node Source của job đó), một câu hỏi
    SAU nhắc lại "MongoDB" phải tìm ra node lân cận qua KG walk — hoàn toàn
    KHÔNG qua vector search (không có `memory_items` nào chứa nội dung này,
    `/v1/memory` không truyền `tier`, không truyền `key`)."""
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "summarize",
                    "spec": {"text": "MongoDB là một Database phổ biến"},
                    "name": "kg-walk-test",
                    "workflow_ref": "agent:summarize.agent@1",
                },
            )
            pid = resp.json()["pid"]
            status = await _wait_for_terminal(client, pid)
            assert status["state"] == "SUCCEEDED"

            results = (
                await client.get("/v1/memory", params={"q": "Tôi muốn hỏi về MongoDB"})
            ).json()
            kg_hits = [r for r in results if r["matched_via"] == "kg_walk"]
            assert len(kg_hits) >= 1
    finally:
        await daemon.stop()
