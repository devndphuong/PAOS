"""Kiểm `paosctl memory show|forget|export|import` nối dây THẬT qua
`build_daemon()` (doc 07 §6, ADR-0029, P-M5-4) — cùng khuôn mẫu
tests/apps/paosd/test_memory_writer_integration.py: đi qua HTTP thật, không
gọi thẳng MemoryStore."""

from __future__ import annotations

from pathlib import Path

import httpx

from apps.paosd.wiring import build_daemon

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine/TimeEngine trả None -> check() luôn
# allowed=True (BL-023, doc 19 P-M7-3). Test memory forget/export/import này
# không kiểm Energy/Time Engine — tránh flaky theo tải CPU/NGÀY GIỜ máy thật.
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")
_MISSING_TIME_POLICY = Path("Z:/paos-test-time-policy-khong-ton-tai.yaml")


async def test_forget_via_http_hard_deletes_and_404s_afterward(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/jobs",
                json={
                    "intent": "video",
                    "name": "job-0",
                    "workflow_ref": "wf@1",
                    "spec": {"duration_sec": 75},
                },
            )
            items = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            assert len(items) == 1
            memory_id = items[0]["memory_id"]

            show_resp = await client.get(f"/v1/memory/{memory_id}")
            assert show_resp.status_code == 200

            forget_resp = await client.delete(f"/v1/memory/{memory_id}")
            assert forget_resp.status_code == 200
            assert forget_resp.json() == {"memory_id": memory_id, "forgotten": True}

            after = await client.get(f"/v1/memory/{memory_id}")
            assert after.status_code == 404

            remaining = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            assert remaining == []
    finally:
        await daemon.stop()


async def test_forget_unknown_id_returns_404(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/v1/memory/mem_khong_ton_tai")
            assert resp.status_code == 404
    finally:
        await daemon.stop()


async def test_export_then_import_roundtrip_via_http(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/v1/jobs",
                json={
                    "intent": "video",
                    "name": "job-0",
                    "workflow_ref": "wf@1",
                    "spec": {"duration_sec": 60},
                },
            )
            export_resp = await client.post("/v1/memory/export")
            assert export_resp.status_code == 200
            body = export_resp.json()
            assert body["count"] == 1
            exported_file = tmp_path / "workspace" / body["path"]
            assert exported_file.is_file()

            import_resp = await client.post(
                "/v1/memory/import",
                json={"items": [{"tier": "L3", "content": "tự sửa tay", "key": "pref.custom"}]},
            )
            assert import_resp.status_code == 200
            assert import_resp.json() == {"count": 1}

            items = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            keys = {i["key"] for i in items}
            assert "pref.custom" in keys
    finally:
        await daemon.stop()
