"""Kiểm MemoryWriter nối dây THẬT qua `build_daemon()` (P-M5-2) — khác
tests/apps/paosd/test_memory_writer.py (đơn vị, EventEnvelope dựng tay):
ở đây `POST /v1/jobs` thật, `EventBus` thật phát `kernel.process.created`
thật, MemoryWriter đăng ký TRƯỚC `events.start()` (`apps/paosd/wiring.py`)
phải tự bắt được — đúng con đường production, không tắt qua constructor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine/TimeEngine trả None -> check() luôn
# allowed=True (BL-023, doc 19 P-M7-3). Test MemoryWriter này không kiểm
# Energy/Time Engine — tránh flaky theo tải CPU/NGÀY GIỜ máy thật lúc chạy.
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")
_MISSING_TIME_POLICY = Path("Z:/paos-test-time-policy-khong-ton-tai.yaml")


async def test_three_jobs_same_spec_value_reach_suggest_via_real_http(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for i in range(3):
                resp = await client.post(
                    "/v1/jobs",
                    json={
                        "intent": "video",
                        "name": f"job-{i}",
                        "workflow_ref": "wf@1",
                        "spec": {"text": "x", "duration_sec": 75},
                    },
                )
                assert resp.status_code == 200

            body = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            duration_items = [b for b in body if b["key"] == "pref.duration_sec"]
            assert len(duration_items) == 1
            item = duration_items[0]
            assert item["content"] == "75"
            assert item["confidence"] == 0.5  # 0.3 + 0.10 + 0.10
            assert item["promotion"] == "suggest"
    finally:
        await daemon.stop()


async def test_artifact_edit_demotes_learned_preference_via_real_http(tmp_path: Path) -> None:
    """doc 13 M5 exit criterion: "Người dùng sửa tay -> confidence giảm"."""
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = (
                await client.post(
                    "/v1/jobs",
                    json={
                        "intent": "video",
                        "name": "job-0",
                        "workflow_ref": "wf@1",
                        "spec": {"duration_sec": 75},
                    },
                )
            ).json()
            process_id = created["process_id"]

            before = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            before_confidence = before[0]["confidence"]

            async def _insert_fake_artifact(conn: Any) -> None:
                await conn.execute(
                    "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
                    "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
                    "VALUES ('art_fake', ?, NULL, 'script', 'artifacts/fake/script.txt', "
                    "'text/plain', 'x', 1, '{}', NULL, NULL, '2026-08-16T00:00:00')",
                    (process_id,),
                )

            await daemon.store.write(_insert_fake_artifact)
            (tmp_path / "workspace" / "artifacts" / "fake").mkdir(parents=True, exist_ok=True)
            (tmp_path / "workspace" / "artifacts" / "fake" / "script.txt").write_text(
                "bản gốc", encoding="utf-8"
            )

            edit_resp = await client.post(
                "/v1/artifacts/art_fake/edited", json={"text": "bản đã sửa hoàn toàn khác"}
            )
            assert edit_resp.status_code == 200

            after = (await client.get("/v1/memory", params={"tier": "L3"})).json()
            after_confidence = after[0]["confidence"]
            assert after_confidence == max(0.0, before_confidence - 0.40)
    finally:
        await daemon.stop()
