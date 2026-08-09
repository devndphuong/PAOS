"""Kiểm M1-3a: N Process chạy song song thật + priority queue + resource token
(doc 19 P-M1-3a, doc 02 §3.2).

`StubAdapter` thật xong <1s — không đủ để CHỨNG MINH chồng lấn bằng đồng hồ
tường. Mọi test ở đây dùng adapter giả truyền qua `build_daemon(adapter_overrides=...)`
(M2-1, `Registry.preload_adapter()`) điều khiển được thời điểm `invoke()` trả
về, quan sát trực tiếp qua biến đếm thay vì suy luận qua timing.
"""

import asyncio
from pathlib import Path
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon
from sdk.provider import CallContext, Estimate, Health, ProviderManifest

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


def _manifest(resources: list[str] | None = None) -> ProviderManifest:
    return ProviderManifest(
        provider_id="stub.deterministic",
        implements=["text.generate@1"],
        provider_class="local",
        privacy="private",
        cost={},
        limits={},
        resources=resources or [],
        health_check={},
        quality_hint={},
    )


class _BlockingAdapter:
    """Mọi lượt `invoke()` cùng chờ một `asyncio.Event` chung — đủ để chứng
    minh "tối đa N lệnh đồng thời", không cần kiểm soát thứ tự chính xác."""

    def __init__(self, resources: list[str] | None = None) -> None:
        self.manifest = _manifest(resources)
        self.release_event = asyncio.Event()
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = asyncio.Lock()

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=0, confidence=1.0)

    async def invoke(self, capability: str, payload: dict[str, Any], ctx: CallContext) -> dict:
        async with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self.release_event.wait()
        finally:
            # finally, không phải sau wait() — task.cancel() (M1-3b) ngắt NGAY tại
            # điểm wait(), nếu đếm ở ngoài try/finally thì job bị hủy không bao
            # giờ được trừ khỏi in_flight.
            async with self._lock:
                self.in_flight -= 1
        return {"text": f"[blocking] {payload['prompt']}", "usage": {}, "meta": {}}

    async def cancel(self, call_id: str) -> None:
        pass


class _StepAdapter:
    """Mỗi lượt `invoke()` chờ đúng 1 item từ `_gate` — `release_one()` mở
    khoá chính xác 1 lượt, cho phép kiểm THỨ TỰ job nào chạy trước."""

    def __init__(self) -> None:
        self.manifest = _manifest()
        self.calls: list[str] = []
        self._gate: asyncio.Queue[None] = asyncio.Queue()

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=0, confidence=1.0)

    async def invoke(self, capability: str, payload: dict[str, Any], ctx: CallContext) -> dict:
        self.calls.append(payload["prompt"])
        await self._gate.get()
        return {"text": f"[step] {payload['prompt']}", "usage": {}, "meta": {}}

    async def cancel(self, call_id: str) -> None:
        pass

    def release_one(self) -> None:
        self._gate.put_nowait(None)


async def _post_job(client: httpx.AsyncClient, text: str, priority: int = 5) -> int:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": text},
            "priority": priority,
            "name": "scheduler-test",
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


async def test_three_processes_run_truly_concurrently(tmp_path: Path) -> None:
    adapter = _BlockingAdapter()

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        max_parallel=3,
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pids = [await _post_job(client, f"job song song {i}") for i in range(3)]

            await _wait_until(lambda: adapter.max_in_flight >= 3)
            assert adapter.max_in_flight == 3

            # job thứ 4 phải KHÔNG vào invoke() được — max_parallel=3 đã kín chỗ.
            pid4 = await _post_job(client, "job thu 4 phai cho")
            await asyncio.sleep(0.05)
            assert adapter.in_flight == 3  # job 4 vẫn đang chờ trong QUEUED

            adapter.release_event.set()

            for pid in [*pids, pid4]:
                status = await _wait_for_terminal(client, pid)
                assert status["state"] == "SUCCEEDED"
    finally:
        await daemon.stop()


async def test_priority_queue_higher_number_runs_first(tmp_path: Path) -> None:
    adapter = _StepAdapter()

    # max_parallel=1 để thứ tự hàng đợi lộ rõ, không lẫn với concurrency.
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        max_parallel=1,
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pid_a = await _post_job(client, "A mac dinh", priority=5)
            await _wait_until(lambda: len(adapter.calls) >= 1)  # A đã chiếm slot duy nhất

            pid_low = await _post_job(client, "B uu tien thap", priority=1)
            pid_high = await _post_job(client, "C uu tien cao", priority=9)
            await asyncio.sleep(0.05)  # để chắc B, C đã nằm trong hàng đợi, chưa chạy
            assert len(adapter.calls) == 1

            adapter.release_one()  # A xong -> slot trống -> job ưu tiên cao hơn chạy trước
            await _wait_until(lambda: len(adapter.calls) >= 2)
            assert "C uu tien cao" in adapter.calls[1]

            adapter.release_one()
            await _wait_until(lambda: len(adapter.calls) >= 3)
            assert "B uu tien thap" in adapter.calls[2]

            adapter.release_one()

            for pid in (pid_a, pid_low, pid_high):
                status = await _wait_for_terminal(client, pid)
                assert status["state"] == "SUCCEEDED"
    finally:
        await daemon.stop()


async def test_resource_token_limits_calls_even_with_free_process_slots(
    tmp_path: Path,
) -> None:
    """3 Process có thể cùng RUNNING (max_parallel=3), nhưng nếu adapter khai
    báo cần resource "gpu" và dung lượng cấu hình chỉ 1, lượt GỌI capability
    thật sự vẫn phải xếp hàng — token gắn với lượt gọi, không phải cả Process."""
    adapter = _BlockingAdapter(resources=["gpu"])

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        max_parallel=3,
        resource_capacity={"gpu": 1},
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pids = [await _post_job(client, f"gpu job {i}") for i in range(3)]

            await _wait_until(lambda: adapter.max_in_flight >= 1)
            await asyncio.sleep(0.1)  # đủ thời gian cho cả 3 process đạt RUNNING nếu không bị chặn
            assert adapter.max_in_flight == 1, "resource token 'gpu' phải chặn còn 1 lượt gọi"

            adapter.release_event.set()

            for pid in pids:
                status = await _wait_for_terminal(client, pid)
                assert status["state"] == "SUCCEEDED"
    finally:
        await daemon.stop()


async def test_cancel_one_of_three_does_not_affect_others(
    tmp_path: Path,
) -> None:
    """Đúng exit criteria doc 13 M1: hủy 1 Process không ảnh hưởng 2 Process
    song song còn lại (doc 19 P-M1-3b)."""
    adapter = _BlockingAdapter()

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        max_parallel=3,
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pids = [await _post_job(client, f"job song song {i}") for i in range(3)]
            await _wait_until(lambda: adapter.max_in_flight >= 3)

            cancel_resp = await client.post(f"/v1/processes/{pids[0]}/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["state"] == "CANCELLED"

            # Bị hủy phải dừng NGAY, không cần đợi release_event.set().
            status0 = (await client.get(f"/v1/processes/{pids[0]}")).json()
            assert status0["state"] == "CANCELLED"
            await _wait_until(lambda: adapter.in_flight == 2)

            adapter.release_event.set()

            for pid in pids[1:]:
                status = await _wait_for_terminal(client, pid)
                assert status["state"] == "SUCCEEDED"
    finally:
        await daemon.stop()


async def test_cancel_queued_process_before_it_starts(tmp_path: Path) -> None:
    """Process còn nằm trong PriorityQueue (chưa có task) vẫn hủy được — và
    khi dispatcher sau đó lấy nó ra, KHÔNG được ghi đè trạng thái CANCELLED."""
    adapter = _BlockingAdapter()

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        max_parallel=1,
        adapter_overrides={"stub.deterministic": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            pid_a = await _post_job(client, "A chiem slot duy nhat")
            await _wait_until(lambda: adapter.in_flight >= 1)

            pid_b = await _post_job(client, "B dang xep hang")
            await asyncio.sleep(0.05)  # chắc B đã vào hàng đợi, chưa được dispatch

            cancel_resp = await client.post(f"/v1/processes/{pid_b}/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["state"] == "CANCELLED"

            adapter.release_event.set()
            status_a = await _wait_for_terminal(client, pid_a)
            assert status_a["state"] == "SUCCEEDED"

            status_b = (await client.get(f"/v1/processes/{pid_b}")).json()
            assert status_b["state"] == "CANCELLED"
    finally:
        await daemon.stop()


async def test_cancel_unknown_pid_returns_404(tmp_path: Path) -> None:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db", workspace_root=tmp_path / "workspace"
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/processes/999999/cancel")
            assert resp.status_code == 404
    finally:
        await daemon.stop()
