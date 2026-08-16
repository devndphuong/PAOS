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
    # tiên.
    WORKFLOW_STEP_SKIPPED = "workflow.step.skipped"
    WORKFLOW_LOOP_ENTERED = "workflow.loop.entered"
    WORKFLOW_COMPENSATION_STARTED = "workflow.compensation.started"
    # Decision Engine (doc 06 §1.1, doc 19 P-M6-1) chọn workflow từ JobSpec —
    # apps/paosd/decision_engine.py::DecisionEngine.select_workflow() là caller
    # thật đầu tiên. Ghi chú ở trên (thêm ở P-M3-2) từng nói "hoãn tới M6" —
    # đây chính là lát cắt đó.
    WORKFLOW_SELECTED = "workflow.selected"

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
    # "artifact.edited" KHÔNG có trong doc 05 gốc — thêm mới ở P-M4-3 (cùng
    # tiền lệ "subtitle.created" ở P-M3-4), đã bổ sung vào bảng doc 05 §3.6.
    # apps/paosd/app.py::edit_artifact là caller thật đầu tiên, sau khi
    # apps/paosd/artifact_store.py::record_edit() đo edit_rate xong.
    ARTIFACT_EDITED = "quality.artifact.edited"

    # Memory & Knowledge (doc 05 §3.7, doc 07, P-M5-1) — doc 05 đã liệt tên
    # này từ đầu dự án. apps/paosd/memory_store.py::MemoryStore.write() là
    # caller thật đầu tiên. "preference.learned"/"consolidated" (doc 05 §3.7)
    # CHƯA thêm — thuộc P-M5-2 (preference learning/consolidation job), chưa
    # có caller thật ở lát cắt này.
    MEMORY_ITEM_WRITTEN = "memory.item.written"
    # "memory.item.forgotten" KHÔNG có trong doc 05 gốc — thêm mới ở P-M5-4
    # (cùng tiền lệ "subtitle.created" ở P-M3-4), đã bổ sung vào bảng doc 05
    # §3.7. apps/paosd/memory_store.py::MemoryStore.forget() là caller thật
    # đầu tiên (doc 07 §6, ADR-0029 — xóa cứng thật, không qua Trash).
    MEMORY_ITEM_FORGOTTEN = "memory.item.forgotten"

    # Knowledge Graph (doc 05 §3.7, doc 07 §4, P-M5-3) — doc 05 đã liệt 3 tên
    # này từ đầu dự án. apps/paosd/knowledge_store.py::KnowledgeStore là
    # caller thật đầu tiên cho cả 3 (create_or_reinforce_node/create_edge).
    KNOWLEDGE_NODE_CREATED = "knowledge.node.created"
    KNOWLEDGE_EDGE_CREATED = "knowledge.edge.created"
    KNOWLEDGE_CONFLICT_DETECTED = "knowledge.conflict.detected"

    # Privacy Filter (doc 07 §6, doc 09 §7, P-M5-4) — KHÔNG có trong doc 05
    # gốc, thêm mới (cùng tiền lệ "quality.artifact.edited" ở P-M4-3), đã bổ
    # sung vào bảng doc 05 §3.9. apps/paosd/router.py::Router.call() là caller
    # thật đầu tiên — phát khi 1 candidate provider `class: cloud` bị loại vì
    # payload mang Memory L3 riêng tư (`contains_private_l3=True`), BẤT KỂ
    # provider đó tự khai `privacy:` gì trong provider.yaml (chống provider
    # khai gian, xem docstring Router._classify()).
    PRIVACY_CLOUD_SEND_BLOCKED = "privacy.cloud_send.blocked"

    # Energy Engine (doc 06 §4, doc 19 P-M7-2, ADR-0030) — doc 06 §4 đã nêu tên
    # này từ đầu ("Process chuyển WAITING, phát resource.wait.started, KHÔNG
    # chạy đè"). apps/paosd/router.py::Router.call() là caller thật đầu tiên —
    # phát khi `EnergyEngine.check()` từ chối 1 candidate (CPU quá tải, đang
    # dùng pin mà capability không nằm trong allowlist, hoặc quá nhiệt — CHỈ
    # tín hiệu THẬT SỰ đo được, ADR-0030). CHƯA chuyển Process sang WAITING
    # thật (cần Router biết ProcessManager hoặc cơ chế event 2 chiều dễ lệch
    # thứ tự — BL-022, docs/backlog.md) — candidate bị loại, thử lại qua
    # backoff/fallback đã có, đúng tinh thần "không chạy đè".
    RESOURCE_WAIT_STARTED = "resource.wait.started"

    # Time Engine (doc 06 §5, doc 19 P-M7-3, ADR-0031) — doc 05 §3.8 đã nêu tên
    # này từ đầu (payload "pid, next_window_at"). apps/paosd/router.py::
    # Router.call() là caller thật đầu tiên — phát khi `TimeEngine.check()`
    # chặn 1 lượt gọi vì đang trong cửa sổ `allow_heavy: false` và capability
    # không nằm trong allowlist của cửa sổ đó. CHƯA chuyển Process sang WAITING
    # thật (cùng lý do RESOURCE_WAIT_STARTED — BL-022, docs/backlog.md).
    TIME_WINDOW_BLOCKED = "time.window.blocked"

    # Savings Report (doc 06 §3.4, doc 19 P-M7-3, ADR-0031) — doc 05 §3.5 đã
    # nêu tên này từ đầu (payload "cache_key, saved_cost"). apps/paosd/
    # router.py::Router.call() là caller thật đầu tiên — phát khi 1 cache hit
    # tránh được 1 lượt gọi thật, `saved_cost` = adapter.estimate() của chính
    # provider đã tạo ra kết quả đang cache (số THẬT, không suy diễn từ giá
    # trung bình — cùng tinh thần ADR-0030 "không giả vờ có số").
    CAPABILITY_CACHE_HIT = "capability.cache.hit"
