"""Cổng CI 2 cần ít nhất một test Kernel không rỗng để chạy khi providers/agents/plugins bị xoá."""

import kernel
from kernel.events.types import EventType


def test_kernel_package_imports() -> None:
    assert kernel is not None


def test_event_type_has_startup_and_shutdown() -> None:
    assert EventType.KERNEL_STARTUP == "kernel.startup"
    assert EventType.KERNEL_SHUTDOWN == "kernel.shutdown"
