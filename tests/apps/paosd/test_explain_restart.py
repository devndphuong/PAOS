"""Test bắt buộc khó nhất của M0 (doc 18 §7.1 #7, doc 19 P-M0-5, R17):
`paosctl explain` sau khi paosd RESTART phải đọc hoàn toàn từ event log,
không từ bộ nhớ tiến trình sống. Mô phỏng restart bằng cách dựng lại toàn bộ
`Daemon` (StateStore/EventBus/ProcessManager/Runner mới) trên CÙNG file DB —
không có gì được giữ lại giữa `daemon1` và `daemon2` ngoài file SQLite.

Từ M1-2 (doc 19), agent chạy nền qua `worker_loop()` — phải poll tới
SUCCEEDED thay vì đọc ngay sau POST.
"""

import asyncio
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from apps.paosd.wiring import build_daemon

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


async def _run_job_to_completion(app: FastAPI) -> int:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/v1/jobs",
            json={
                "intent": "summarize",
                "spec": {"text": "PAOS là một hệ điều hành AI cá nhân chạy local-first."},
                "name": "cli-summarize",
                "workflow_ref": "agent:summarize.agent@1",
            },
        )
        created = resp.json()
        pid: int = created["pid"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        state = "CREATED"
        while state not in _TERMINAL_STATES:
            if loop.time() > deadline:
                raise TimeoutError(f"process pid={pid} không xong sau 5s")
            state = (await client.get(f"/v1/processes/{pid}")).json()["state"]
            if state not in _TERMINAL_STATES:
                await asyncio.sleep(0.01)
        assert state == "SUCCEEDED"
        return pid


async def _explain(app: FastAPI, pid: int) -> list[dict[str, Any]]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/v1/processes/{pid}/explain")
        assert resp.status_code == 200
        result: list[dict[str, Any]] = resp.json()["trace"]
        return result


async def test_explain_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / ".paos" / "state.db"
    workspace_root = tmp_path / "workspace"

    daemon1 = await build_daemon(db_path, workspace_root=workspace_root)
    pid = await _run_job_to_completion(daemon1.app)

    trace_before_restart = await _explain(daemon1.app, pid)
    await daemon1.stop()

    # "Restart": KHÔNG tái sử dụng bất kỳ đối tượng Python nào của daemon1 — dựng
    # lại hoàn toàn StateStore/EventBus/ProcessManager/Runner mới, chỉ chung file DB.
    daemon2 = await build_daemon(db_path, workspace_root=workspace_root)
    try:
        trace_after_restart = await _explain(daemon2.app, pid)
    finally:
        await daemon2.stop()

    assert trace_after_restart == trace_before_restart
    assert len(trace_after_restart) == 6
    assert [e["type"] for e in trace_after_restart] == [
        "kernel.process.created",
        "kernel.process.planning",
        "kernel.process.queued",
        "kernel.process.started",
        "summary.created",
        "kernel.process.completed",
    ]


async def test_explain_after_restart_matches_live_status(tmp_path: Path) -> None:
    """Vế thứ hai của R17: trạng thái đọc được sau restart phải khớp trạng thái
    cuối cùng quan sát được lúc process còn sống — không lệch dù object gốc đã mất."""
    db_path = tmp_path / ".paos" / "state.db"
    workspace_root = tmp_path / "workspace"

    daemon1 = await build_daemon(db_path, workspace_root=workspace_root)
    pid = await _run_job_to_completion(daemon1.app)
    await daemon1.stop()

    daemon2 = await build_daemon(db_path, workspace_root=workspace_root)
    try:
        transport = httpx.ASGITransport(app=daemon2.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = await client.get(f"/v1/processes/{pid}")
            assert status.json()["state"] == "SUCCEEDED"
    finally:
        await daemon2.stop()
