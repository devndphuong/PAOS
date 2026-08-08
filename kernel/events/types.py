"""Danh sách tập trung mọi loại Event (doc 05).

Không nơi nào khác được viết chuỗi tên event trần.
"""

from enum import StrEnum


class EventType(StrEnum):
    KERNEL_STARTUP = "kernel.startup"
    KERNEL_SHUTDOWN = "kernel.shutdown"

    # Process (doc 05 §3.1). "queued" không có trong catalog gốc — thêm ở M0
    # lát cắt 3 vì QUEUED là trạng thái có đường vào thật (doc 19 P-M0-3).
    PROCESS_CREATED = "kernel.process.created"
    PROCESS_QUEUED = "kernel.process.queued"
    PROCESS_STARTED = "kernel.process.started"
    PROCESS_COMPLETED = "kernel.process.completed"
    PROCESS_FAILED = "kernel.process.failed"
    PROCESS_CANCELLED = "kernel.process.cancelled"
