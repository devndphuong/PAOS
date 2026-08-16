"""Kiểm Runner + wiring thật — đường vàng M0 (doc 13): `paosctl run` -> artifact
-> `explain` hiện trace. Từ M1-2 (doc 19), agent chạy NỀN qua worker_loop() —
`POST /v1/jobs` chỉ đảm bảo QUEUED, nên mọi test đợi qua `_wait_for_terminal()`
thay vì check ngay sau POST. Không mock gì — StubAdapter thật, Registry thật,
StateStore thật."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import httpx
import pytest

from apps.paosd import runner as runner_module
from apps.paosd.wiring import Daemon, build_daemon
from kernel.events.bus import EventBus
from kernel.process.manager import ProcessManager, ProcessState
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.agent import (
    AgentContext,
    AgentManifest,
    Artifact,
    ExecResult,
    Plan,
    ReviewResult,
    ValidationResult,
)
from sdk.provider import Estimate, Health, ProviderError

# File KHÔNG BAO GIỜ tồn tại — EnergyEngine/TimeEngine trả None -> check() luôn
# allowed=True (BL-023, doc 19 P-M7-3). Test Runner/wiring đường vàng này
# không kiểm Energy/Time Engine — tránh flaky theo tải CPU/NGÀY GIỜ máy thật.
_MISSING_ENERGY_POLICY = Path("Z:/paos-test-energy-policy-khong-ton-tai.yaml")
_MISSING_TIME_POLICY = Path("Z:/paos-test-time-policy-khong-ton-tai.yaml")

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class _CheckpointableAgent:
    """Test double cho resume() (P-M3-1) — Runner chỉ có SummarizeAgent thật,
    manifest.checkpointable=False, không tự chứng minh được đường resume()."""

    manifest = AgentManifest(
        agent_id="resume_test.agent",
        version=1,
        needs=[],
        produces=["x"],
        capabilities=[],
        emits=[],
        checkpointable=True,
    )

    def __init__(self) -> None:
        self.resume_called_with: dict[str, Any] | None = None
        self.validate_called = False
        self._ctx: AgentContext | None = None

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        self.validate_called = True
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        return Plan(prompt="")

    async def execute(self, plan: Plan) -> ExecResult:
        return ExecResult(data={"out": "from_execute"})

    async def review(self, result: ExecResult) -> ReviewResult:
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        assert self._ctx is not None
        return [await self._ctx.write_artifact("x", "out.txt", result.data["out"])]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        self.resume_called_with = checkpoint
        return ExecResult(data={"out": f"resumed:{checkpoint.get('n')}"})


@pytest.fixture
async def daemon(tmp_path: Path):
    d = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    yield d
    await d.stop()


@pytest.fixture
async def client(daemon: Daemon):
    transport = httpx.ASGITransport(app=daemon.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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


async def test_golden_path_run_produces_artifact_and_explain_trace(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "PAOS là một hệ điều hành AI cá nhân chạy local-first."},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    assert explain_resp.status_code == 200
    trace = explain_resp.json()["trace"]
    assert [e["type"] for e in trace] == [
        "kernel.process.created",
        "kernel.process.planning",
        "kernel.process.queued",
        "kernel.process.started",
        "kernel.process.checkpointed",
        "summary.created",
        "kernel.process.completed",
    ]
    artifact_id = trace[5]["payload"]["artifact_id"]
    assert artifact_id.startswith("art_")


async def test_concurrent_processes_same_agent_do_not_corrupt_each_other(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test đối kháng BL-007 (docs/backlog.md): `Registry.load_agent()` cache 1
    instance Agent dùng chung theo `agent_id@version`, nhưng Agent gán state
    riêng-theo-lượt lên `self` (`self._ctx` trong `initialize()`). `worker_loop()`
    chạy nhiều Process THẬT SỰ đồng thời (`asyncio.Semaphore(max_parallel)`,
    mặc định 3) — nếu chúng dùng chung 1 agent instance, `self._ctx` của
    process này có thể bị process khác ghi đè giữa chừng trước khi `publish()`
    kịp đọc lại, khiến artifact/event bị publish nhầm process.

    `PAOS_STUB_DELAY_MS` ép `StubAdapter.invoke()` (bên trong `execute()`) chờ
    thật thay vì trả ngay — mở đủ khe hở để 3 process cùng chạy `initialize()`
    (đồng bộ, không await) TRƯỚC KHI bất kỳ process nào tới lượt `publish()`;
    không có khe hở này race gần như không thể ép lộ ra tất định trong 1 lượt
    test (mọi bước trước execute() đều đồng bộ, chạy hết trong 1 lượt event
    loop, không có cơ hội xen kẽ)."""
    monkeypatch.setenv("PAOS_STUB_DELAY_MS", "30")

    texts = [f"VAN-BAN-DOC-LAP-SO-{i}-{'x' * 20}" for i in range(3)]
    created = []
    for i, text in enumerate(texts):
        resp = await client.post(
            "/v1/jobs",
            json={
                "intent": "summarize",
                "spec": {"text": text},
                "name": f"race-{i}",
                "workflow_ref": "agent:summarize.agent@1",
            },
        )
        assert resp.status_code == 200
        created.append(resp.json())

    statuses = await asyncio.gather(
        *(_wait_for_terminal(client, c["pid"], max_wait_s=10.0) for c in created)
    )

    workspace_root = tmp_path / "workspace"
    for c, text, status in zip(created, texts, statuses, strict=True):
        assert status["state"] == "SUCCEEDED", f"process {c['pid']} không SUCCEEDED: {status}"

        explain_resp = await client.get(f"/v1/processes/{c['pid']}/explain")
        trace = explain_resp.json()["trace"]
        own_created_events = [e for e in trace if e["type"] == "summary.created"]
        assert len(own_created_events) == 1, (
            f"process {c['pid']} phải có ĐÚNG 1 sự kiện summary.created của "
            f"chính nó, thấy {len(own_created_events)} — dấu hiệu self._ctx bị "
            f"process khác ghi đè giữa chừng (BL-007)"
        )

        artifact_file = workspace_root / "artifacts" / c["process_id"] / "summary.txt"
        assert artifact_file.is_file(), (
            f"process {c['pid']} không có artifact summary.txt trong đúng thư "
            f"mục process_id của chính nó — publish() đã ghi nhầm sang process "
            f"khác (BL-007)"
        )
        content = artifact_file.read_text(encoding="utf-8")
        assert text in content, (
            f"artifact của process {c['pid']} chứa nội dung KHÁC văn bản đã "
            f"gửi — lẫn dữ liệu process khác (BL-007): mong '{text}' trong "
            f"{content!r}"
        )


async def test_workflow_ref_golden_path_runs_full_dag_to_succeeded(
    client: httpx.AsyncClient,
) -> None:
    """P-M3-2: `workflow:<id>@<version>` đi qua WorkflowRunner (DAG capability
    -> agent), khác hẳn `agent:<id>@<version>` (1 agent = cả Process). Dùng
    `workflows/demo.pipeline/1/workflow.yaml` thật trong repo (chỉ ghép lại
    text.generate@1 + summarize.agent@1 đã có từ M0/M2, không cần agent mới)."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "demo",
            "spec": {"text": "PAOS là một hệ điều hành AI cá nhân chạy local-first."},
            "name": "cli-demo-pipeline",
            "workflow_ref": "workflow:demo.pipeline@1",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    trace = explain_resp.json()["trace"]
    types = [e["type"] for e in trace]
    assert "kernel.task.scheduled" in types
    assert types.count("kernel.task.completed") == 2  # draft + summary
    assert "summary.created" in types
    assert types[-1] == "kernel.process.completed"


async def test_video_plan_and_script_golden_path_runs_two_real_agents(
    client: httpx.AsyncClient,
) -> None:
    """P-M3-3: Video plugin phần 1 — PlanningAgent -> ScriptAgent, 2 agent THẬT
    (không phải capability giả lập như demo.pipeline), chứng minh P4: thêm
    ScriptAgent không sửa PlanningAgent, không sửa WorkflowRunner. Agent nạp
    động qua Registry.load_agent() (P-M3-4, trả nợ BL-006) — không còn dict
    tĩnh nào trong Runner để "thêm 1 dòng" nữa."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "video",
            "spec": {"text": "MongoDB là cơ sở dữ liệu NoSQL hướng tài liệu."},
            "name": "cli-video-plan-script",
            "workflow_ref": "workflow:video.plan_and_script@1",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    trace = explain_resp.json()["trace"]
    types = [e["type"] for e in trace]
    assert types.count("kernel.task.scheduled") == 2  # plan + script
    assert "plan.created" in types
    assert "script.created" in types
    assert types.index("plan.created") < types.index("script.created")
    assert types[-1] == "kernel.process.completed"


async def test_video_plan_script_media_golden_path_runs_parallel_branch(
    client: httpx.AsyncClient,
) -> None:
    """P-M3-4: version 2 của `video.plan_and_script` thêm nhánh song song
    Image ∥ Voice ∥ Subtitle sau Script — version 1 (P-M3-3) giữ nguyên,
    không sửa (doc 04 §6: đóng băng bản đã chạy). Kiểm luôn "explain" (Event
    Log) mang được thông tin tiết kiệm thời gian, không cần công cụ nào khác."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "video",
            "spec": {"text": "MongoDB là cơ sở dữ liệu NoSQL hướng tài liệu."},
            "name": "cli-video-media",
            "workflow_ref": "workflow:video.plan_and_script@2",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"], max_wait_s=10.0)
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    trace = explain_resp.json()["trace"]
    types = [e["type"] for e in trace]
    assert "image.batch.created" in types
    assert "voice.created" in types
    assert "subtitle.created" in types

    media_completed = next(
        e
        for e in trace
        if e["type"] == "kernel.task.completed" and e["payload"]["step_id"] == "media"
    )
    parallel = media_completed["payload"]["parallel"]
    assert parallel["saved_ms"] == max(0, parallel["sequential_ms"] - parallel["wall_ms"])


async def test_uc1_end_to_end_offline_full_pipeline(client: httpx.AsyncClient) -> None:
    """P-M3-5 — doc 01 §3 UC1, doc 13 M3 exit criteria: Planning -> Script ->
    (Image ∥ Voice ∥ Subtitle) -> Render, offline 100% (toàn bộ provider stub/
    local, không cần mạng/GPU thật). Kiểm luôn progress() có caller thật
    (P-M3-1) và tín hiệu tiết kiệm thời gian (P-M3-4) cùng có mặt trong 1
    lượt chạy đầy đủ."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "video",
            "spec": {"text": "MongoDB là cơ sở dữ liệu NoSQL hướng tài liệu."},
            "name": "cli-uc1",
            "workflow_ref": "workflow:video.plan_and_script@3",
        },
    )
    assert resp.status_code == 200
    created = resp.json()

    status = await _wait_for_terminal(client, created["pid"], max_wait_s=10.0)
    assert status["state"] == "SUCCEEDED"

    explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
    trace = explain_resp.json()["trace"]
    types = [e["type"] for e in trace]
    assert types.index("plan.created") < types.index("script.created")
    assert "image.batch.created" in types
    assert "voice.created" in types
    assert "subtitle.created" in types
    assert "kernel.process.progress" in types
    video_rendered = next(e for e in trace if e["type"] == "video.rendered")
    assert video_rendered["payload"]["degraded"] is False
    assert types[-1] == "kernel.process.completed"


async def test_uc1_degraded_mode_when_image_generate_has_no_gpu(tmp_path: Path) -> None:
    """doc 13 M3 exit criteria LOC-05: chạy UC1 khi `image.generate` không có
    GPU khả dụng (giả lập) -> workflow vẫn hoàn tất ở chế độ degraded, không
    crash, không lỗi âm thầm — `video.rendered.degraded=True` là tín hiệu rõ
    ràng, `kernel.task.failed`/`workflow.step.skipped` ghi lại lý do thật."""

    class _NoGpuAdapter:
        manifest = SimpleNamespace(resources=[])

        async def health(self) -> Health:
            return Health(healthy=False)

        async def estimate(self, capability: str, payload: dict) -> Estimate:
            return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

        async def invoke(self, capability: str, payload: dict, ctx) -> dict:
            raise ProviderError(
                "RESOURCE_EXHAUSTED",
                "không có GPU khả dụng (giả lập, test LOC-05)",
                retryable=False,
                hint="test",
            )

        async def cancel(self, call_id: str) -> None:
            pass

    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={"stub.image": _NoGpuAdapter()},
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "video",
                    "spec": {"text": "MongoDB là cơ sở dữ liệu NoSQL hướng tài liệu."},
                    "name": "cli-uc1-degraded",
                    "workflow_ref": "workflow:video.plan_and_script@3",
                },
            )
            assert resp.status_code == 200
            created = resp.json()

            status = await _wait_for_terminal(client, created["pid"], max_wait_s=10.0)
            assert status["state"] == "SUCCEEDED"  # KHÔNG crash, KHÔNG fail cả Process

            explain_resp = await client.get(f"/v1/processes/{created['pid']}/explain")
            trace = explain_resp.json()["trace"]
            types = [e["type"] for e in trace]

            assert "kernel.task.failed" in types  # lỗi image GHI LẠI THẬT, không nuốt
            skipped = next(e for e in trace if e["type"] == "workflow.step.skipped")
            assert skipped["payload"]["step_id"] == "image"
            assert "image.batch.created" not in types  # không có ảnh nào được publish

            video_rendered = next(e for e in trace if e["type"] == "video.rendered")
            assert video_rendered["payload"]["degraded"] is True
            assert "voice.created" in types  # nhánh khác vẫn chạy bình thường
            assert "subtitle.created" in types
    finally:
        await daemon.stop()


async def test_unknown_workflow_id_fails_clean(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": "workflow:no_such_workflow@1"},
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"
    assert body["error_code"] == "NOT_FOUND"


async def test_post_jobs_returns_before_agent_finishes(client: httpx.AsyncClient) -> None:
    """ADR-0026: response POST /v1/jobs nghĩa là "đã tạo và đưa vào hàng đợi",
    KHÔNG còn nghĩa "đã chạy xong" — đây là điểm khác biệt cốt lõi của M1-2."""
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "văn bản để tóm tắt"},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    created = resp.json()
    immediate = (await client.get(f"/v1/processes/{created['pid']}")).json()
    assert immediate["state"] in {"QUEUED", "RUNNING", "SUCCEEDED"}

    status = await _wait_for_terminal(client, created["pid"])
    assert status["state"] == "SUCCEEDED"


async def test_unknown_workflow_ref_fails_clean(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": "agent:no_such_agent@1"},
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"
    assert body["error_code"] == "NOT_FOUND"


async def test_empty_text_fails_with_invalid_input(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/jobs",
        json={
            "intent": "summarize",
            "spec": {"text": "   "},
            "name": "cli-summarize",
            "workflow_ref": "agent:summarize.agent@1",
        },
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"
    assert body["error_code"] == "INVALID_INPUT"


async def test_error_message_is_redacted_before_persisted(
    client: httpx.AsyncClient, daemon: Daemon
) -> None:
    """workflow_ref do caller tự do đặt — chứng minh redact() thật sự chạy trước
    khi error_message ghi vào processes.error_json (doc 09 §6, SEC-01, doc 19
    P-M2-5), không chỉ đúng trên lý thuyết đọc code."""
    leaked = "sk-abcdefgh12345678"
    resp = await client.post(
        "/v1/jobs",
        json={"intent": "x", "name": "a", "workflow_ref": f"agent:no_such_{leaked}@1"},
    )
    created = resp.json()

    body = await _wait_for_terminal(client, created["pid"])
    assert body["state"] == "FAILED"

    async def _select(conn: aiosqlite.Connection) -> str:
        cursor = await conn.execute(
            "SELECT error_json FROM processes WHERE pid = ?", (created["pid"],)
        )
        row = await cursor.fetchone()
        assert row is not None
        return str(row[0])

    error_json = await daemon.store.read(_select)
    assert leaked not in error_json
    assert "***REDACTED***" in error_json


@pytest.fixture
async def bare_runner(tmp_path: Path):
    """Runner KHÔNG subscribe on_process_created, KHÔNG chạy worker_loop() —
    khác `daemon` fixture. Test resume() cần tự tay đưa Process qua từng
    trạng thái + tự ghi checkpoint TRƯỚC khi gọi `_run_one()` một lần duy
    nhất, không muốn đua với dispatcher nền thật (doc 19 P-M3-1)."""
    store = StateStore(tmp_path / ".paos" / "state.db")
    await store.start()
    events = EventBus(store)
    registry = Registry(tmp_path / "capabilities", tmp_path / "providers")
    registry.load()
    manager = ProcessManager(store, events)
    runner = runner_module.Runner(
        manager,
        events,
        registry,
        store,
        tmp_path / "workspace",
        energy_policy_path=_MISSING_ENERGY_POLICY,
        time_policy_path=_MISSING_TIME_POLICY,
    )
    yield manager, runner, registry
    await store.stop()


async def test_resume_called_when_running_process_has_agent_checkpoint(
    bare_runner: tuple[Any, Any, Registry], tmp_path: Path
) -> None:
    """Mô phỏng daemon restart giữa lúc RUNNING với agent checkpointable=True đã
    tự ghi checkpoint (seq > 1, qua ctx.checkpoint()) — Runner (P-M3-1) phải gọi
    resume() thay vì chạy lại validate()/think()/execute() từ đầu."""
    manager, runner, registry = bare_runner
    agent = _CheckpointableAgent()
    registry.preload_agent("resume_test.agent", 1, agent, tmp_path)

    process = await manager.create(
        intent="x", spec={}, name="r", workflow_ref="agent:resume_test.agent@1"
    )
    await manager.transition(process.process_id, ProcessState.PLANNING)
    await manager.transition(process.process_id, ProcessState.QUEUED)
    await manager.transition(process.process_id, ProcessState.RUNNING)
    await manager.write_checkpoint(process.process_id, {"phase": "running"})  # seq 1
    await manager.write_checkpoint(process.process_id, {"n": 5})  # seq 2 — agent tự ghi

    await runner._run_one(process.process_id)

    assert agent.resume_called_with == {"n": 5}
    assert agent.validate_called is False
    final = await manager.get(process.process_id)
    assert final is not None
    assert final.state == ProcessState.SUCCEEDED


async def test_resume_not_called_when_only_placeholder_checkpoint_exists(
    bare_runner: tuple[Any, Any, Registry], tmp_path: Path
) -> None:
    """Cùng kịch bản restart nhưng agent CHƯA tự ghi checkpoint nào (chỉ có mốc
    "phase: running" seq=1 do Runner ghi sẵn) — không đủ để resume(), phải chạy
    lại từ đầu (đúng hành vi M1-4 cho tới khi agent thật sự checkpoint)."""
    manager, runner, registry = bare_runner
    agent = _CheckpointableAgent()
    registry.preload_agent("resume_test.agent", 1, agent, tmp_path)

    process = await manager.create(
        intent="x", spec={}, name="r", workflow_ref="agent:resume_test.agent@1"
    )
    await manager.transition(process.process_id, ProcessState.PLANNING)
    await manager.transition(process.process_id, ProcessState.QUEUED)
    await manager.transition(process.process_id, ProcessState.RUNNING)
    await manager.write_checkpoint(process.process_id, {"phase": "running"})  # seq 1 duy nhất

    await runner._run_one(process.process_id)

    assert agent.resume_called_with is None
    assert agent.validate_called is True
    final = await manager.get(process.process_id)
    assert final is not None
    assert final.state == ProcessState.SUCCEEDED
