"""Kiểm PluginProcess — spawn subprocess THẬT (fake_plugin.py), JSON-RPC qua
stdio, crash + tự restart, timeout (doc 09 §5, ADR-0032, doc 19 P-M8-1).

Dùng `python fake_plugin.py` (tests/kernel/fixtures/plugin_process/) làm
subprocess THẬT — không giả lập `asyncio.create_subprocess_exec`, đúng tinh
thần "chạy thật" (RSK-01) cho đúng cơ chế IPC mới nhất của dự án."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.plugin.process import PluginProcess

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "plugin_process"
_ENTRY = f"{sys.executable} fake_plugin.py"


@pytest.fixture(autouse=True)
def _clear_fake_plugin_env() -> None:
    os.environ.pop("FAKE_PLUGIN_CRASH_ON", None)
    os.environ.pop("FAKE_PLUGIN_DELAY_S", None)
    yield
    os.environ.pop("FAKE_PLUGIN_CRASH_ON", None)
    os.environ.pop("FAKE_PLUGIN_DELAY_S", None)


async def test_call_health_returns_result() -> None:
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    try:
        result = await process.call("health", {})
        assert result == {"healthy": True, "detail": None}
    finally:
        await process.stop()


async def test_call_invoke_returns_result() -> None:
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    try:
        result = await process.call("invoke", {"capability": "text.generate@1", "payload": {}})
        assert result == {"text": "fake-plugin-output"}
    finally:
        await process.stop()


async def test_call_unknown_method_raises_paos_error() -> None:
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    try:
        with pytest.raises(PaosError) as exc_info:
            await process.call("khong_ton_tai", {})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
    finally:
        await process.stop()


async def test_process_reused_across_calls() -> None:
    """1 subprocess phục vụ NHIỀU lượt gọi tuần tự (không spawn lại mỗi lần)."""
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    try:
        await process.call("health", {})
        pid_first = process._process.pid  # type: ignore[union-attr]
        await process.call("health", {})
        pid_second = process._process.pid  # type: ignore[union-attr]
        assert pid_first == pid_second
    finally:
        await process.stop()


async def test_crash_publishes_on_crash_and_restarts_automatically() -> None:
    os.environ["FAKE_PLUGIN_CRASH_ON"] = "invoke"
    crashes: list[tuple[str, str]] = []

    async def _on_crash(plugin_id: str, detail: str) -> None:
        crashes.append((plugin_id, detail))

    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR, on_crash=_on_crash)
    try:
        with pytest.raises(PaosError):
            await process.call("invoke", {"capability": "x", "payload": {}})
        assert len(crashes) == 1
        assert crashes[0][0] == "demo"

        # Lượt gọi kế tiếp không crash -> tự khởi động lại thành công, không
        # cần can thiệp tay (doc 09 §5 "tự restart tối đa 3 lần").
        os.environ.pop("FAKE_PLUGIN_CRASH_ON")
        result = await process.call("health", {})
        assert result == {"healthy": True, "detail": None}
    finally:
        await process.stop()


async def test_crash_exceeding_max_restarts_becomes_permanently_dead() -> None:
    os.environ["FAKE_PLUGIN_CRASH_ON"] = "invoke"
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    try:
        for _ in range(4):  # doc 09 §5: tối đa 3 lần restart -> lần thứ 4 chết hẳn
            with pytest.raises(PaosError):
                await process.call("invoke", {"capability": "x", "payload": {}})

        with pytest.raises(PaosError) as exc_info:
            await process.call("invoke", {"capability": "x", "payload": {}})
        assert exc_info.value.code == ErrorCode.PROVIDER_DOWN
        assert "không tự khởi động lại" in exc_info.value.message
    finally:
        await process.stop()


async def test_call_timeout_raises_provider_timeout_and_triggers_crash() -> None:
    os.environ["FAKE_PLUGIN_DELAY_S"] = "2"
    crashes: list[tuple[str, str]] = []

    async def _on_crash(plugin_id: str, detail: str) -> None:
        crashes.append((plugin_id, detail))

    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR, wall_sec=0.2, on_crash=_on_crash)
    try:
        with pytest.raises(PaosError) as exc_info:
            await process.call("health", {})
        assert exc_info.value.code == ErrorCode.PROVIDER_TIMEOUT
        assert len(crashes) == 1
    finally:
        await process.stop()
