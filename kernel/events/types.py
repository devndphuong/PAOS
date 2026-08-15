"""Danh sách tập trung mọi loại Event (doc 05).

Không nơi nào khác được viết chuỗi tên event trần.
"""

from enum import StrEnum


class EventType(StrEnum):
    KERNEL_STARTUP = "kernel.startup"
    KERNEL_SHUTDOWN = "kernel.shutdown"

    # Process (doc 05 §3.1). "queued" không có trong catalog gốc — thêm ở M0
    # lát cắt 3 vì QUEUED là trạng thái có đường vào thật (doc 19 P-M0-3).
    # planning/waiting/resumed/compensating/failed_final thêm ở M1 lát cắt 1
    # (doc 19 P-M1-1) cùng lý do — doc 05 gốc cũng thiếu các event này.
    PROCESS_CREATED = "kernel.process.created"
    PROCESS_PLANNING = "kernel.process.planning"
    PROCESS_QUEUED = "kernel.process.queued"
    PROCESS_STARTED = "kernel.process.started"
    # doc 05 §3.1 đã có tên "checkpointed" nhưng chưa từng có schema — thêm ở
    # M1 lát cắt 4 (doc 19 P-M1-4) khi checkpoint thật sự được ghi.
    PROCESS_CHECKPOINTED = "kernel.process.checkpointed"
    PROCESS_WAITING = "kernel.process.waiting"
    PROCESS_PAUSED = "kernel.process.paused"
    PROCESS_RESUMED = "kernel.process.resumed"
    PROCESS_COMPLETED = "kernel.process.completed"
    PROCESS_FAILED = "kernel.process.failed"
    PROCESS_COMPENSATING = "kernel.process.compensating"
    PROCESS_FAILED_FINAL = "kernel.process.failed_final"
    PROCESS_CANCELLED = "kernel.process.cancelled"
    # doc 05 §3.1 dòng "throttle ≤1/giây — chưa phát ở M1" — thêm thật ở
    # P-M3-1 khi AgentContext.progress() có caller đầu tiên.
    PROCESS_PROGRESS = "kernel.process.progress"

    # Domain event do plugin/agent tự định nghĩa qua manifest.emits (doc 05 §3.4).
    SUMMARY_CREATED = "summary.created"

    # Router (doc 06 §2.3, doc 19 P-M2-3): 1 provider lỗi retryable, chuyển
    # sang provider kế tiếp trong chuỗi fallback.
    CAPABILITY_FALLBACK_TRIGGERED = "capability.fallback.triggered"

    # Permission Guard (doc 09 §2/§3/§4, doc 19 P-M2-5).
    PERMISSION_VIOLATION_BLOCKED = "permission.violation.blocked"
    PERMISSION_APPROVAL_REQUESTED = "permission.approval.requested"
