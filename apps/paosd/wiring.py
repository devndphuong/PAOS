"""Nối StateStore + EventBus + Registry + ProcessManager + Runner + app HTTP
thành một daemon hoàn chỉnh (doc 19 P-M0-5, lát 5c).

Dùng chung bởi `apps/paosd/__main__.py` (chạy thật) và test: gọi lại
`build_daemon()` trên CÙNG file DB mô phỏng chính xác một lần restart thật
(doc 18 §7.1 #7 / R17) — không có đường tắt nào khác giữa 2 nơi gọi.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from apps.paosd.app import create_app
from apps.paosd.artifact_store import ArtifactStore
from apps.paosd.knowledge_extractor import KnowledgeExtractor
from apps.paosd.knowledge_store import KnowledgeStore
from apps.paosd.memory_store import MemoryStore
from apps.paosd.memory_writer import MemoryWriter
from apps.paosd.runner import Runner
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.process.manager import ProcessManager, ProcessState
from kernel.registry.registry import Registry
from kernel.state.db import StateStore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAOS_VERSION = "0.0.1"  # khớp [project].version ở pyproject.toml


@dataclass
class Daemon:
    store: StateStore
    events: EventBus
    registry: Registry
    manager: ProcessManager
    runner: Runner
    app: FastAPI
    started_at: float
    worker_task: asyncio.Task[None]

    async def stop(self) -> None:
        """Tắt có kiểm soát: hủy worker trước — KHÔNG đợi job đang chạy xong,
        nhất quán với triết lý "kill -9 rồi phục hồi" đã kiểm ở M0/M1-1 (resume
        thật từ checkpoint là M1-4, ngoài phạm vi M1-2) — rồi phát kernel.shutdown,
        rồi mới đóng StateStore (event này cần actor loop còn sống để ghi được)."""
        self.worker_task.cancel()
        try:
            await self.worker_task
        except asyncio.CancelledError:
            pass
        await self.events.publish(
            EventType.KERNEL_SHUTDOWN.value,
            source="paosd",
            payload={"version": _PAOS_VERSION, "uptime": time.monotonic() - self.started_at},
        )
        await self.store.stop()


async def build_daemon(
    db_path: Path,
    workspace_root: Path | None = None,
    *,
    max_parallel: int = 3,
    resource_capacity: dict[str, int] | None = None,
    adapter_overrides: dict[str, Any] | None = None,
) -> Daemon:
    """`workspace_root` mặc định `<repo>/workspace` cho daemon thật — test truyền
    `tmp_path` riêng để không ghi artifact vào cây thư mục repo thật.
    `max_parallel`/`resource_capacity` xem `apps/paosd/runner.py::Runner` (M1-3a).
    `adapter_overrides` ({provider_id: instance}) chỉ dùng cho test — thay
    adapter thật bằng adapter giả điều khiển được (`Registry.preload_adapter()`,
    M2-1), production code không truyền tham số này."""
    started_at = time.monotonic()
    store = StateStore(db_path)
    await store.start()

    events = EventBus(store)
    registry = Registry(
        _REPO_ROOT / "capabilities",
        _REPO_ROOT / "providers",
        _REPO_ROOT / "workflows",
        _REPO_ROOT / "agents",
        rubrics_dir=_REPO_ROOT / "rubrics",
    )
    registry.load()
    for provider_id, adapter in (adapter_overrides or {}).items():
        registry.preload_adapter(provider_id, adapter)
    resolved_workspace_root = workspace_root or _REPO_ROOT / "workspace"
    manager = ProcessManager(store, events)
    runner = Runner(
        manager,
        events,
        registry,
        store,
        resolved_workspace_root,
        max_parallel=max_parallel,
        resource_capacity=resource_capacity,
    )
    events.subscribe("runner", "kernel.process.created", runner.on_process_created)

    # MemoryWriter (P-M5-2, doc 07 §2) — quan sát Event để tự học sở thích.
    # 2 tên subscriber riêng vì EventBus.subscribe() ánh xạ 1 tên -> ĐÚNG 1
    # pattern (kernel/events/bus.py::_subscribers dict) — không gộp được 2
    # pattern khác nhau dưới cùng 1 tên.
    memory_writer = MemoryWriter(MemoryStore(store, events))
    events.subscribe(
        "memory_writer_process", "kernel.process.created", memory_writer.on_process_created
    )
    events.subscribe(
        "memory_writer_edit", "quality.artifact.edited", memory_writer.on_artifact_edited
    )

    # KnowledgeExtractor (P-M5-3, doc 07 §4) — quan sát 3 event tạo-artifact
    # THẬT đã có caller (agents/planning|script|summarize), trích entity vào
    # Knowledge Graph. 3 tên subscriber riêng — cùng lý do 2 tên của
    # MemoryWriter ở trên (EventBus.subscribe ánh xạ 1 tên -> ĐÚNG 1 pattern),
    # cả 3 trỏ về CÙNG 1 handler (logic giống hệt nhau, chỉ khác nguồn event).
    knowledge_extractor = KnowledgeExtractor(
        KnowledgeStore(store, events), ArtifactStore(store, resolved_workspace_root)
    )
    events.subscribe(
        "knowledge_extractor_plan", "plan.created", knowledge_extractor.on_artifact_event
    )
    events.subscribe(
        "knowledge_extractor_script", "script.created", knowledge_extractor.on_artifact_event
    )
    events.subscribe(
        "knowledge_extractor_summary", "summary.created", knowledge_extractor.on_artifact_event
    )

    await events.start()  # giao lại event chưa dispatch cho từng subscriber (REL-01) —
    # quan trọng nếu daemon crash giữa lúc xử lý event chưa kịp xong.

    worker_task = asyncio.create_task(runner.worker_loop())

    # asyncio.Queue của Runner sống trong bộ nhớ, không sống sót qua restart —
    # khác với event catch-up ở trên (đã "delivered" nên không redeliver). Process
    # nào còn ở QUEUED từ phiên daemon trước phải đưa lại vào hàng đợi thủ công
    # (doc 19 P-M1-2). Process kẹt ở RUNNING lúc crash cũng đưa lại — Runner tự
    # nhận ra đây là resume (không transition lại) và chạy lại agent từ đầu
    # (doc 19 P-M1-4, Process = 1 agent nên không có tiến độ nội bộ để tiếp).
    for state in (ProcessState.QUEUED, ProcessState.RUNNING):
        stuck = await manager.list(state=state)
        for process in stuck:
            await runner.enqueue_existing(process.process_id)

    await events.publish(
        EventType.KERNEL_STARTUP.value,
        source="paosd",
        payload={"version": _PAOS_VERSION, "uptime": 0},
    )

    app = create_app(manager, events, runner, store, resolved_workspace_root)
    return Daemon(
        store=store,
        events=events,
        registry=registry,
        manager=manager,
        runner=runner,
        app=app,
        started_at=started_at,
        worker_task=worker_task,
    )
