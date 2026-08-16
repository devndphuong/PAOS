"""Idempotency ở mức Process (doc 13 M1 exit criteria: "Task chạy lại 2 lần
-> 1 bản ghi chi phí", REL-06, doc 19 P-M1-6).

Task/Cost Ledger CHƯA tồn tại như khái niệm runtime ở M1 — Task DAG nhiều
bước là M3, Cost Engine ghi vào `cost_entries` là M7 (bảng vừa "chừa cột" ở
M1-6, `kernel/state/migrations/003_cost_and_decisions.sql`, chưa ai ghi vào).
Process = 1 agent là đơn vị công việc duy nhất tồn tại thật ở M1, nên test ở
đây kiểm ở granularity đó: kích hoạt lại CÙNG một process đã xong (qua
`events replay`, M1-5b) không được tạo artifact trùng, không đổi trạng thái
đã kết thúc — đúng cơ chế idempotent đã xây ở `on_process_created()` (M1-4).
"""

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import httpx

from apps.paosd.wiring import build_daemon

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine/TimeEngine trả None -> check() luôn
# allowed=True (BL-023, doc 19 P-M7-3). Test idempotency (replay event) này
# không kiểm Energy/Time Engine — tránh flaky theo tải CPU/NGÀY GIỜ máy thật.
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")
_MISSING_TIME_POLICY = Path("Z:/paos-test-time-policy-khong-ton-tai.yaml")


async def _post_job(client: httpx.AsyncClient, text: str) -> dict[str, Any]:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": text},
            "name": "idempotency-test",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    body: dict[str, Any] = resp.json()
    return body


async def _wait_for_terminal(
    client: httpx.AsyncClient, pid: int, max_wait_s: float = 5.0
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    body: dict[str, Any] = {}
    while loop.time() < deadline:
        body = (await client.get(f"/v1/processes/{pid}")).json()
        if body["state"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return body
        await asyncio.sleep(0.01)
    return body


async def test_replaying_completed_process_does_not_duplicate_artifact(
    tmp_path: Path,
) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await _post_job(client, "văn bản kiểm idempotency")
            pid = created["pid"]
            process_id = created["process_id"]

            status = await _wait_for_terminal(client, pid)
            assert status["state"] == "SUCCEEDED"

            async def _count_artifacts(conn: aiosqlite.Connection) -> int:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM artifacts WHERE process_id = ?", (process_id,)
                )
                row = await cursor.fetchone()
                assert row is not None
                return int(row[0])

            count_before = await daemon.store.read(_count_artifacts)
            assert count_before == 1

            # kernel.process.created đã "delivered" từ lần chạy thật — replay
            # chủ động giao lại LẦN NỮA cho "runner", process ĐÃ SUCCEEDED.
            replay_resp = await client.post(
                "/v1/events/replay",
                json={
                    "from_ts": "0001-01-01T00:00:00",
                    "to_ts": "9999-12-31T23:59:59",
                    "to_subscriber": "runner",
                },
            )
            assert replay_resp.status_code == 200
            assert replay_resp.json()["replayed"] >= 1

            count_after = await daemon.store.read(_count_artifacts)
            assert count_after == count_before  # KHÔNG tạo thêm artifact

            status_after = (await client.get(f"/v1/processes/{pid}")).json()
            assert status_after["state"] == "SUCCEEDED"  # không chạy lại, không đổi
    finally:
        await daemon.stop()
