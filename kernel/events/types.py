"""Danh sách tập trung mọi loại Event (doc 05).

Không nơi nào khác được viết chuỗi tên event trần.
"""

from enum import StrEnum


class EventType(StrEnum):
    KERNEL_STARTUP = "kernel.startup"
    KERNEL_SHUTDOWN = "kernel.shutdown"
