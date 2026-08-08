"""Nối StateStore + EventBus + Registry + ProcessManager + Runner + app HTTP
thành một daemon hoàn chỉnh (doc 19 P-M0-5, lát 5c).

Dùng chung bởi `apps/paosd/__main__.py` (chạy thật) và test: gọi lại
`build_daemon()` trên CÙNG file DB mô phỏng chính xác một lần restart thật
(doc 18 §7.1 #7 / R17) — không có đường tắt nào khác giữa 2 nơi gọi.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from apps.paosd.app import create_app
from apps.paosd.runner import Runner
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.process.manager import ProcessManager
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

    async def stop(self) -> None:
        """Tắt có kiểm soát: phát kernel.shutdown TRƯỚC KHI đóng StateStore — event
        này cần actor loop còn sống để ghi được."""
        await self.events.publish(
            EventType.KERNEL_SHUTDOWN.value,
            source="paosd",
            payload={"version": _PAOS_VERSION, "uptime": time.monotonic() - self.started_at},
        )
        await self.store.stop()


async def build_daemon(db_path: Path, workspace_root: Path | None = None) -> Daemon:
    """`workspace_root` mặc định `<repo>/workspace` cho daemon thật — test truyền
    `tmp_path` riêng để không ghi artifact vào cây thư mục repo thật."""
    started_at = time.monotonic()
    store = StateStore(db_path)
    await store.start()

    events = EventBus(store)
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    manager = ProcessManager(store, events)
    runner = Runner(manager, events, registry, store, workspace_root or _REPO_ROOT / "workspace")
    events.subscribe("runner", "kernel.process.created", runner.on_process_created)

    await events.start()  # giao lại event chưa dispatch cho subscriber "runner" (REL-01) —
    # quan trọng nếu daemon crash giữa lúc một process đang RUNNING.

    await events.publish(
        EventType.KERNEL_STARTUP.value,
        source="paosd",
        payload={"version": _PAOS_VERSION, "uptime": 0},
    )

    app = create_app(manager, events)
    return Daemon(
        store=store,
        events=events,
        registry=registry,
        manager=manager,
        runner=runner,
        app=app,
        started_at=started_at,
    )
