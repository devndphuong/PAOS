"""Test bắt buộc của M1 (doc 13): `kill -9` giữa Process đang RUNNING → khởi
động lại resume từ checkpoint, 0 event mất (doc 19 P-M1-4).

Process = đúng 1 agent (chưa có Task DAG nhiều bước — M3), nên "resume" ở
đây nghĩa là: không kẹt RUNNING vĩnh viễn, tự chạy lại agent từ đầu — không
phải resume giữa chừng một bước cụ thể. Mô phỏng crash THẬT: không gọi
`Daemon.stop()` gọn gàng (sẽ cancel task + phát kernel.shutdown "đàng hoàng"),
mà tự hủy task nền rồi đóng StateStore thô, giữ nguyên `state=RUNNING` trong
DB như một crash thật để lại.
"""

import asyncio
import contextlib
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon
from kernel.events.bus import EventBus
from kernel.process.manager import ProcessManager, ProcessState
from sdk.provider import CallContext, Estimate, Health, ProviderManifest

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class _BlockingAdapter:
    def __init__(self) -> None:
        self.manifest = ProviderManifest(
            provider_id="stub.deterministic",
            implements=["text.generate@1"],
            provider_class="local",
            privacy="private",
            cost={},
            limits={},
            resources=[],
            health_check={},
            quality_hint={},
        )
        self.release_event = asyncio.Event()
        self.in_flight = 0
        self.call_count = 0

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=0, confidence=1.0)

    async def invoke(self, capability: str, payload: dict[str, Any], ctx: CallContext) -> dict:
        self.call_count += 1
        self.in_flight += 1
        try:
            await self.release_event.wait()
        finally:
            self.in_flight -= 1
        return {"text": f"[blocking] {payload['prompt']}", "usage": {}, "meta": {}}

    async def cancel(self, call_id: str) -> None:
        pass


async def _post_job(client: httpx.AsyncClient, text: str) -> int:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": text},
            "name": "resume-test",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    pid: int = resp.json()["pid"]
    return pid


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


async def _wait_until(predicate: Any, max_wait_s: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    while not predicate():
        if loop.time() > deadline:
            raise TimeoutError("điều kiện không đạt trong thời gian chờ")
        await asyncio.sleep(0.01)


async def _simulate_crash(daemon: Any) -> None:
    """KHÔNG gọi `daemon.stop()` (cancel gọn + phát kernel.shutdown) — tự hủy
    task nền rồi đóng StateStore thô, giữ nguyên `state=RUNNING` trong DB như
    một crash thật để lại."""
    daemon.worker_task.cancel()
    for task in list(daemon.runner._running_tasks.values()):
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await daemon.worker_task
    await daemon.store.stop()


def _read_state_and_event_count(db_path: Path, pid: int) -> tuple[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT state FROM processes WHERE pid = ?", (pid,)).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert row is not None
    return row[0], int(count)


async def test_resume_after_crash_while_running(tmp_path: Path) -> None:
    adapter1 = _BlockingAdapter()

    db_path = tmp_path / ".paos" / "state.db"
    workspace_root = tmp_path / "workspace"

    daemon1 = await build_daemon(
        db_path,
        workspace_root=workspace_root,
        adapter_overrides={"stub.deterministic": adapter1},
    )
    transport1 = httpx.ASGITransport(app=daemon1.app)
    async with httpx.AsyncClient(transport=transport1, base_url="http://test") as client:
        pid = await _post_job(client, "van ban bi crash giua chung")
        await _wait_until(lambda: adapter1.in_flight >= 1)  # đã thật sự vào RUNNING

        checkpoint_status = (await client.get(f"/v1/processes/{pid}")).json()
        assert checkpoint_status["state"] == "RUNNING"

    await _simulate_crash(daemon1)

    # Xác nhận đã "chết" thật giữa RUNNING trước khi phục hồi — không phải giả định.
    state, events_before = _read_state_and_event_count(db_path, pid)
    assert state == "RUNNING"

    # Adapter MỚI, không dùng lại adapter1 (đã "chết" cùng daemon1) — mô phỏng
    # daemon thật khởi động lại với provider mới nạp.
    adapter2 = _BlockingAdapter()

    daemon2 = await build_daemon(
        db_path,
        workspace_root=workspace_root,
        adapter_overrides={"stub.deterministic": adapter2},
    )
    try:
        transport2 = httpx.ASGITransport(app=daemon2.app)
        async with httpx.AsyncClient(transport=transport2, base_url="http://test") as client:
            # Tự chạy lại từ đầu — không cần gọi API nào thêm.
            await _wait_until(lambda: adapter2.in_flight >= 1)
            adapter2.release_event.set()

            status = await _wait_for_terminal(client, pid)
            assert status["state"] == "SUCCEEDED"

            explain = (await client.get(f"/v1/processes/{pid}/explain")).json()
            trace_types = [e["type"] for e in explain["trace"]]
            # 0 event mất (REL-01): created/planning/queued/started/checkpointed ghi
            # TRƯỚC crash vẫn còn nguyên — resume KHÔNG transition lại RUNNING (đã
            # ở RUNNING từ trước, xem docstring _run_one), nên "started" chỉ 1 lần
            # dù thực thi agent lại từ đầu.
            assert trace_types.count("kernel.process.created") == 1
            assert trace_types.count("kernel.process.checkpointed") == 1
            assert trace_types.count("kernel.process.started") == 1
            assert trace_types[-1] == "kernel.process.completed"
    finally:
        _, events_after = _read_state_and_event_count(db_path, pid)
        assert events_after > events_before  # event cũ còn nguyên, chỉ thêm chứ không mất
        await daemon2.stop()


async def test_on_process_created_idempotent_after_crash_between_planning_and_queued(
    tmp_path: Path,
) -> None:
    """Regression — phát hiện lúc kiểm thủ công M1-4: crash đúng giữa
    on_process_created() (đã ghi PLANNING, chưa kịp QUEUED) khiến catch-up
    (REL-01) gọi lại hàm này lúc restart. Nếu transition() vô điều kiện,
    lần gọi lại CONFLICT vì PLANNING không tự chuyển sang chính nó — process
    kẹt PLANNING vĩnh viễn (event đã bị đánh dấu "failed", không bao giờ
    retry lần 3). Test này gọi lại y hệt catch-up sẽ làm, xác nhận không kẹt."""
    adapter = _BlockingAdapter()

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        # EventBus RIÊNG, KHÔNG subscribe "runner" — để tạo process mà không tự
        # động kích hoạt on_process_created thật (ta cần tự điều khiển state để
        # mô phỏng đúng điểm crash).
        isolated_events = EventBus(daemon.store)
        isolated_manager = ProcessManager(daemon.store, isolated_events)
        process = await isolated_manager.create(
            intent="summarize",
            spec={"text": "idempotent test"},
            name="x",
            workflow_ref="agent:summarize.agent@1",
        )
        # Mô phỏng "on_process_created đã chạy được nửa đường rồi crash": tự
        # transition tới PLANNING bằng tay, KHÔNG qua on_process_created thật.
        await daemon.manager.transition(process.process_id, ProcessState.PLANNING)

        trace = await daemon.events.events_for_process(process.process_id)
        created_envelope = next(e for e in trace if e.type == "kernel.process.created")

        # Gọi lại đúng như EventBus.start() catch-up sẽ làm lúc restart.
        await daemon.runner.on_process_created(created_envelope)

        updated = await daemon.manager.get(process.process_id)
        assert updated is not None
        assert updated.state is ProcessState.QUEUED  # không kẹt ở PLANNING

        adapter.release_event.set()
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = await _wait_for_terminal(client, process.pid)
            assert status["state"] == "SUCCEEDED"
    finally:
        await daemon.stop()


async def test_duplicate_enqueue_does_not_run_agent_twice(tmp_path: Path) -> None:
    """Nếu on_process_created() được gọi lại (catch-up) SAU KHI process đã tới
    QUEUED — idempotent nên vẫn đi tới nhánh enqueue lần nữa. worker_loop()
    phải tự phát hiện process_id đã có task đang chạy và bỏ qua bản trùng,
    không chạy agent 2 lần (P9)."""
    adapter = _BlockingAdapter()

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        process = await daemon.manager.create(
            intent="summarize",
            spec={"text": "duplicate enqueue test"},
            name="x",
            workflow_ref="agent:summarize.agent@1",
        )
        trace = await daemon.events.events_for_process(process.process_id)
        created_envelope = next(e for e in trace if e.type == "kernel.process.created")

        # Process đã CREATED->PLANNING->QUEUED->enqueue thật (qua subscriber bình
        # thường lúc create()). Gọi lại thêm 1 lần y hệt catch-up trùng lặp.
        await daemon.runner.on_process_created(created_envelope)

        await _wait_until(lambda: adapter.in_flight >= 1)
        await asyncio.sleep(0.05)  # đủ thời gian để bản trùng (nếu có) cũng vào invoke()
        assert adapter.call_count == 1  # KHÔNG chạy đúp dù bị enqueue 2 lần

        adapter.release_event.set()
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            status = await _wait_for_terminal(client, process.pid)
            assert status["state"] == "SUCCEEDED"
        assert adapter.call_count == 1
    finally:
        await daemon.stop()
