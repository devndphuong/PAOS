"""Kiểm `GET /v1/artifacts/{id}` + `POST /v1/artifacts/{id}/edited` (doc 08 §5,
doc 13 M4 exit criteria, P-M4-3) — dùng daemon THẬT (build_daemon), chạy
workflow `script_with_review` THẬT để có 1 artifact `script` thật (không tạo
artifact tay), cùng khuôn mẫu tests/apps/paosd/test_self_correction.py."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon
from sdk.eval import edit_rate
from sdk.provider import Estimate, Health

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_SCRIPT_TEXT = " ".join(f"tu{i}" for i in range(178)) + "\n\n## CTA\nHãy đăng ký kênh."


class _PassOnFirstAttemptAdapter:
    """Khớp CẢ generate lẫn judge — điểm luôn cao, review pass ngay vòng 1
    (không cần lặp), giữ test này tập trung vào artifact edit, không phải
    self-correction (đã kiểm kỹ ở test_self_correction.py)."""

    manifest = SimpleNamespace(resources=[])

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def cancel(self, call_id: str) -> None:
        pass

    async def invoke(self, capability: str, payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
        if payload.get("task_class") == "quality_judge":
            verdict = '{"score": 95, "issue": null, "suggestion": null, "keep_note": "tốt"}'
            return {"text": verdict, "usage": {}, "meta": {}}
        return {"text": _SCRIPT_TEXT, "usage": {}, "meta": {}}


async def _wait_for_terminal(
    client: httpx.AsyncClient, pid: int, max_wait_s: float = 10.0
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


async def _run_workflow_and_get_script_artifact_id(
    client: httpx.AsyncClient,
) -> tuple[str, int, str]:
    """Trả (process_id, pid, script_artifact_id) sau khi chạy workflow thật tới PASS."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "video",
            "spec": {"plan": "Giới thiệu một quán cà phê nhỏ"},
            "name": "test-artifact-edit",
            "workflow_ref": "workflow:script_with_review@1",
        },
    )
    created = resp.json()
    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"
    explain = (await client.get(f"/v1/processes/{created['pid']}/explain")).json()
    script_created = next(e for e in explain["trace"] if e["type"] == "script.created")
    return created["process_id"], created["pid"], script_created["payload"]["artifact_id"]


async def test_get_artifact_returns_content(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={
            "stub.deterministic": _PassOnFirstAttemptAdapter(),
            "ollama.qwen2.5-14b": _PassOnFirstAttemptAdapter(),
        },
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, _, artifact_id = await _run_workflow_and_get_script_artifact_id(client)

            resp = await client.get(f"/v1/artifacts/{artifact_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["artifact_id"] == artifact_id
            assert body["type"] == "script"
            assert body["content"] == _SCRIPT_TEXT
    finally:
        await daemon.stop()


async def test_get_artifact_not_found(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/artifacts/art_khong_ton_tai")
            assert resp.status_code == 404
    finally:
        await daemon.stop()


async def test_edit_artifact_records_edit_rate_and_supersedes(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={
            "stub.deterministic": _PassOnFirstAttemptAdapter(),
            "ollama.qwen2.5-14b": _PassOnFirstAttemptAdapter(),
        },
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            process_id, pid, artifact_id = await _run_workflow_and_get_script_artifact_id(client)

            edited_text = _SCRIPT_TEXT.replace("tu0 tu1 tu2", "sửa tu1 tu2")
            resp = await client.post(
                f"/v1/artifacts/{artifact_id}/edited", json={"text": edited_text}
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["edited_artifact_id"] != artifact_id
            expected_rate = edit_rate(_SCRIPT_TEXT, edited_text)
            assert body["edit_rate"] == expected_rate

            # bản mới đọc lại đúng nội dung đã sửa, supersedes bản gốc (ADR-0013).
            edited_resp = await client.get(f"/v1/artifacts/{body['edited_artifact_id']}")
            assert edited_resp.status_code == 200
            assert edited_resp.json()["content"] == edited_text

            async def _select_supersedes(conn: Any) -> Any:
                cursor = await conn.execute(
                    "SELECT supersedes FROM artifacts WHERE artifact_id = ?",
                    (body["edited_artifact_id"],),
                )
                return await cursor.fetchone()

            row = await daemon.store.read(_select_supersedes)
            assert row[0] == artifact_id

            async def _select_edit_row(conn: Any) -> Any:
                cursor = await conn.execute(
                    "SELECT original_artifact_id, edited_artifact_id, edit_rate, process_id "
                    "FROM artifact_edits WHERE original_artifact_id = ?",
                    (artifact_id,),
                )
                return await cursor.fetchone()

            edit_row = await daemon.store.read(_select_edit_row)
            assert edit_row[0] == artifact_id
            assert edit_row[1] == body["edited_artifact_id"]
            assert edit_row[2] == expected_rate
            assert edit_row[3] == process_id

            explain = (await client.get(f"/v1/processes/{pid}/explain")).json()
            edited_events = [e for e in explain["trace"] if e["type"] == "quality.artifact.edited"]
            assert len(edited_events) == 1
            assert edited_events[0]["payload"]["artifact_id"] == artifact_id
            assert edited_events[0]["payload"]["edited_artifact_id"] == body["edited_artifact_id"]
            # Event payload làm tròn 4 chữ số (apps/paosd/app.py::edit_artifact) — response
            # HTTP giữ độ chính xác đầy đủ, chỉ event mới làm tròn cho gọn.
            assert edited_events[0]["payload"]["edit_rate"] == round(expected_rate, 4)
    finally:
        await daemon.stop()


async def test_edit_artifact_identical_text_has_zero_edit_rate(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={
            "stub.deterministic": _PassOnFirstAttemptAdapter(),
            "ollama.qwen2.5-14b": _PassOnFirstAttemptAdapter(),
        },
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            _, _, artifact_id = await _run_workflow_and_get_script_artifact_id(client)
            resp = await client.post(
                f"/v1/artifacts/{artifact_id}/edited", json={"text": _SCRIPT_TEXT}
            )
            assert resp.status_code == 200
            assert resp.json()["edit_rate"] == 0.0
    finally:
        await daemon.stop()


async def test_edit_artifact_not_found(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/artifacts/art_khong_ton_tai/edited", json={"text": "sửa gì đó"}
            )
            assert resp.status_code == 404
    finally:
        await daemon.stop()
