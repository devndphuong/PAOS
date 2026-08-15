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

    # doc 05 §3.1 "kernel.task.*" — thêm thật ở P-M3-2 khi Task (doc 03 §2.3)
    # có caller đầu tiên: kernel/process/tasks.py::TaskStore.
    TASK_SCHEDULED = "kernel.task.scheduled"
    TASK_STARTED = "kernel.task.started"
    TASK_COMPLETED = "kernel.task.completed"
    TASK_FAILED = "kernel.task.failed"
    TASK_RETRIED = "kernel.task.retried"

    # doc 05 §3.2 Workflow & Decision — thêm thật ở P-M3-2 khi Workflow YAML
    # engine (kernel/workflow/, apps/paosd/workflow_runner.py) có caller đầu
    # tiên. workflow.selected (Decision Engine chọn workflow) hoãn tới M6 —
    # M3 nhận workflow_ref trực tiếp từ caller, chưa có gì để "chọn".
    WORKFLOW_STEP_SKIPPED = "workflow.step.skipped"
    WORKFLOW_LOOP_ENTERED = "workflow.loop.entered"
    WORKFLOW_COMPENSATION_STARTED = "workflow.compensation.started"

    # Domain event do plugin/agent tự định nghĩa qua manifest.emits (doc 05 §3.4).
    SUMMARY_CREATED = "summary.created"
    # doc 05 §3.4 đã liệt tên này từ đầu dự án — agents/planning/, agents/script/
    # (P-M3-3) là caller thật đầu tiên.
    PLAN_CREATED = "plan.created"
    SCRIPT_CREATED = "script.created"
    # doc 05 §3.4 đã liệt "image.batch.created"/"voice.created" từ đầu dự án —
    # agents/image/, agents/voice/ (P-M3-4) là caller thật đầu tiên.
    # "subtitle.created" KHÔNG có trong doc 05 gốc — thêm mới ở P-M3-4, đã bổ
    # sung vào bảng doc 05 §3.4 (cùng tiền lệ "queued" ở M0, "planning" ở M1-1).
    IMAGE_BATCH_CREATED = "image.batch.created"
    VOICE_CREATED = "voice.created"
    SUBTITLE_CREATED = "subtitle.created"
    # doc 05 §3.4 đã liệt "video.rendered" từ đầu dự án — agents/render/
    # (P-M3-5) là caller thật đầu tiên, khép kín UC1 (doc 01 §3).
    VIDEO_RENDERED = "video.rendered"

    # Router (doc 06 §2.3, doc 19 P-M2-3): 1 provider lỗi retryable, chuyển
    # sang provider kế tiếp trong chuỗi fallback.
    CAPABILITY_FALLBACK_TRIGGERED = "capability.fallback.triggered"

    # Permission Guard (doc 09 §2/§3/§4, doc 19 P-M2-5).
    PERMISSION_VIOLATION_BLOCKED = "permission.violation.blocked"
    PERMISSION_APPROVAL_REQUESTED = "permission.approval.requested"

    # Quality & Self-Correction (doc 05 §3.4, doc 08 §3/§4, doc 19 P-M4-2) —
    # agents/review/ (Review Agent) là caller thật đầu tiên cho 3 event đầu;
    # QUALITY_ESCALATED_TO_HUMAN do apps/paosd/workflow_runner.py::_run_self_correction
    # phát (quyết định VÒNG LẶP, không phải 1 lượt chấm — không thuộc về Review
    # Agent, xem docstring agents/review/agent.py).
    QUALITY_REVIEW_STARTED = "quality.review.started"
    QUALITY_REVIEW_PASSED = "quality.review.passed"
    QUALITY_REVIEW_REJECTED = "quality.review.rejected"
    QUALITY_ESCALATED_TO_HUMAN = "quality.escalated.to_human"
