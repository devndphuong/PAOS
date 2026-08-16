"""Kiểm paosctl — CLI thật gọi HTTP thật tới paosd thật (doc 19 P-M0-5, lát 5c).

Dựng uvicorn trong một thread nền, RIÊNG event loop (qua `asyncio.run()` bên
trong thread đó) — không tái sử dụng loop của pytest-asyncio. `build_daemon()`
PHẢI được gọi bên trong cùng loop sẽ phục vụ HTTP, vì StateStore's actor task
và `asyncio.Queue` không chia sẻ được giữa 2 loop khác nhau (bài học từ
`tests/apps/paosd/test_app.py`, xem docstring ở đó). Nhờ đó `paosctl` được
kiểm bằng HTTP THẬT — đúng ràng buộc doc 04 §1 "CLI chỉ nói chuyện qua HTTP".
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from click.testing import CliRunner

from apps.paosctl.__main__ import cli
from apps.paosd.memory_store import MemoryStore
from apps.paosd.wiring import Daemon, build_daemon
from providers.stub_embed.adapter import _embed


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class _ReadyServer(uvicorn.Server):
    """`uvicorn.Server` không có sẵn cách await "đã sẵn sàng" — ghi đè `startup()`
    để bắn `asyncio.Event` thay vì phải poll `self.started` (ASYNC110)."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self.ready.set()


class _BackgroundDaemon(threading.Thread):
    def __init__(
        self,
        db_path: Path,
        workspace_root: Path,
        port: int,
        *,
        seed: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self._db_path = db_path
        self._workspace_root = workspace_root
        self.port = port
        self._seed = seed
        self._started = threading.Event()
        self._server: _ReadyServer | None = None

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        daemon = await build_daemon(self._db_path, workspace_root=self._workspace_root)
        if self._seed is not None:
            # PHẢI chạy TRONG cùng loop với daemon (bài học docstring đầu file)
            # — không thể seed từ loop của test sau khi fixture trả về URL.
            await self._seed(daemon)
        config = uvicorn.Config(daemon.app, host="127.0.0.1", port=self.port, log_level="warning")
        server = _ReadyServer(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._server = server
        serve_task = asyncio.create_task(server.serve())
        await server.ready.wait()
        self._started.set()
        await serve_task
        await daemon.stop()

    def wait_ready(self, timeout: float = 5.0) -> None:
        if not self._started.wait(timeout):
            raise RuntimeError("uvicorn không khởi động kịp trong test")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        self.join(timeout=5)


@pytest.fixture
def api_url(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    server = _BackgroundDaemon(tmp_path / ".paos" / "state.db", tmp_path / "workspace", port)
    server.start()
    server.wait_ready()
    yield f"http://127.0.0.1:{port}"
    server.stop()


async def _seed_memory(daemon: Daemon) -> None:
    ms = MemoryStore(daemon.store, daemon.events)
    await ms.write(tier="L3", content="thích video 60-90 giây", key="pref.duration")
    target = "tone chuyên nghiệp trong video"
    await ms.write(
        tier="L3", content=target, key="pref.tone", embedding=(_embed(target), "stub.embed")
    )


@pytest.fixture
def api_url_with_memory(tmp_path: Path) -> Iterator[str]:
    port = _free_port()
    server = _BackgroundDaemon(
        tmp_path / ".paos" / "state.db", tmp_path / "workspace", port, seed=_seed_memory
    )
    server.start()
    server.wait_ready()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_doctor_reports_ok(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "doctor"])
    assert result.exit_code == 0
    assert "✓" in result.output


def test_doctor_reports_connection_failure(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--api-url", "http://127.0.0.1:1", "doctor"])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_run_summarizes_and_reports_success(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(
        cli,
        ["--api-url", api_url, "run", "PAOS là một hệ điều hành AI cá nhân chạy local-first."],
    )
    assert result.exit_code == 0
    assert "Hoàn tất" in result.output
    assert "paosctl explain" in result.output


def test_run_empty_text_reports_failure(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "run", "   "])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_ps_lists_created_process(runner: CliRunner, api_url: str) -> None:
    runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    result = runner.invoke(cli, ["--api-url", api_url, "ps"])
    assert result.exit_code == 0
    assert "SUCCEEDED" in result.output


def test_cancel_unknown_pid_reports_error(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "cancel", "999999"])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_cancel_already_finished_pid_reports_conflict(runner: CliRunner, api_url: str) -> None:
    run_result = runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    pid = run_result.output.splitlines()[0].split("pid=")[1].split(" ")[0]

    result = runner.invoke(cli, ["--api-url", api_url, "cancel", pid])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_status_shows_process_detail(runner: CliRunner, api_url: str) -> None:
    run_result = runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    pid = run_result.output.splitlines()[0].split("pid=")[1].split(" ")[0]

    result = runner.invoke(cli, ["--api-url", api_url, "status", pid])
    assert result.exit_code == 0
    assert "state: SUCCEEDED" in result.output


def test_status_unknown_pid_fails_clean(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "status", "999999"])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_explain_shows_full_trace(runner: CliRunner, api_url: str) -> None:
    run_result = runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    pid = run_result.output.splitlines()[0].split("pid=")[1].split(" ")[0]

    result = runner.invoke(cli, ["--api-url", api_url, "explain", pid])
    assert result.exit_code == 0
    assert "kernel.process.created" in result.output
    assert "summary.created" in result.output
    assert "kernel.process.completed" in result.output


def test_events_tail_without_follow_returns_one_batch(runner: CliRunner, api_url: str) -> None:
    runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    result = runner.invoke(cli, ["--api-url", api_url, "events", "tail"])
    assert result.exit_code == 0
    assert "kernel.process.created" in result.output


def test_events_dlq_empty_by_default(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "events", "dlq"])
    assert result.exit_code == 0
    assert "không có event nào" in result.output


def test_events_replay_to_runner_reports_count(runner: CliRunner, api_url: str) -> None:
    runner.invoke(cli, ["--api-url", api_url, "run", "văn bản để tóm tắt"])
    result = runner.invoke(
        cli,
        [
            "--api-url",
            api_url,
            "events",
            "replay",
            "--from",
            "0001-01-01T00:00:00",
            "--to",
            "9999-12-31T23:59:59",
            "--to-subscriber",
            "runner",
        ],
    )
    assert result.exit_code == 0
    assert "Đã giao lại" in result.output


def _run_and_get_artifact_id(runner: CliRunner, api_url: str) -> str:
    run_result = runner.invoke(
        cli, ["--api-url", api_url, "run", "PAOS là một hệ điều hành AI cá nhân chạy local-first."]
    )
    pid = run_result.output.splitlines()[0].split("pid=")[1].split(" ")[0]
    explain = runner.invoke(cli, ["--api-url", api_url, "explain", pid])
    line = next(line for line in explain.output.splitlines() if "summary.created" in line)
    return line.split('"artifact_id": "')[1].split('"')[0]


def test_artifact_show_prints_content(runner: CliRunner, api_url: str) -> None:
    artifact_id = _run_and_get_artifact_id(runner, api_url)
    result = runner.invoke(cli, ["--api-url", api_url, "artifact", "show", artifact_id])
    assert result.exit_code == 0
    assert artifact_id in result.output
    assert "summary" in result.output


def test_artifact_show_unknown_id_reports_error(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url, "artifact", "show", "art_khong_ton_tai"])
    assert result.exit_code == 1
    assert "✗" in result.output


def test_artifact_edit_reports_edit_rate(runner: CliRunner, api_url: str, tmp_path: Path) -> None:
    artifact_id = _run_and_get_artifact_id(runner, api_url)
    edited_file = tmp_path / "edited.txt"
    edited_file.write_text("bản đã tự tay sửa hoàn toàn khác", encoding="utf-8")

    result = runner.invoke(
        cli, ["--api-url", api_url, "artifact", "edit", artifact_id, str(edited_file)]
    )
    assert result.exit_code == 0
    assert "edit_rate=" in result.output

    # bản sửa đọc lại được qua chính `artifact show`.
    edited_artifact_id = result.output.split("Đã ghi bản sửa ")[1].split(" ")[0]
    show_result = runner.invoke(cli, ["--api-url", api_url, "artifact", "show", edited_artifact_id])
    assert "bản đã tự tay sửa hoàn toàn khác" in show_result.output


def test_artifact_edit_unknown_id_reports_error(
    runner: CliRunner, api_url: str, tmp_path: Path
) -> None:
    edited_file = tmp_path / "edited.txt"
    edited_file.write_text("nội dung", encoding="utf-8")
    result = runner.invoke(
        cli, ["--api-url", api_url, "artifact", "edit", "art_khong_ton_tai", str(edited_file)]
    )
    assert result.exit_code == 1
    assert "✗" in result.output


def test_events_replay_unknown_subscriber_reports_error(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(
        cli,
        [
            "--api-url",
            api_url,
            "events",
            "replay",
            "--from",
            "0001-01-01T00:00:00",
            "--to",
            "9999-12-31T23:59:59",
            "--to-subscriber",
            "chua_dang_ky",
        ],
    )
    assert result.exit_code == 1
    assert "✗" in result.output


def test_memory_list_shows_seeded_items(runner: CliRunner, api_url_with_memory: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url_with_memory, "memory", "list", "L3"])
    assert result.exit_code == 0
    assert "pref.duration" in result.output
    assert "pref.tone" in result.output


def test_memory_list_empty_tier_reports_none(runner: CliRunner, api_url_with_memory: str) -> None:
    result = runner.invoke(cli, ["--api-url", api_url_with_memory, "memory", "list", "L4"])
    assert result.exit_code == 0
    assert "không có ký ức nào" in result.output


def test_memory_search_finds_similar_item(runner: CliRunner, api_url_with_memory: str) -> None:
    result = runner.invoke(
        cli,
        [
            "--api-url",
            api_url_with_memory,
            "memory",
            "search",
            "tone chuyên nghiệp trong video",
            "--tier",
            "L3",
        ],
    )
    assert result.exit_code == 0
    assert "tone chuyên nghiệp trong video" in result.output
    assert "vector" in result.output


def test_memory_search_no_match_reports_none(runner: CliRunner, api_url: str) -> None:
    result = runner.invoke(
        cli, ["--api-url", api_url, "memory", "search", "câu bất kỳ", "--tier", "L3"]
    )
    assert result.exit_code == 0
    assert "không tìm thấy" in result.output
