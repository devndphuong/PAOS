"""Danh sách tập trung mọi loại Event — không nơi nào khác được viết chuỗi tên event trần (doc 05)."""

from enum import StrEnum


class EventType(StrEnum):
    KERNEL_STARTUP = "kernel.startup"
    KERNEL_SHUTDOWN = "kernel.shutdown"
