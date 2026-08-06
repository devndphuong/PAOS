# 05 — Event Catalog

**Trạng thái:** v1.0 · Event Schema là hợp đồng dài hạn (P10).

---

## 1. Envelope chuẩn

Mọi event đều có đúng cấu trúc này:

```json
{
  "event_id": "evt_01J8ZQ...",
  "type": "agent.script.completed",
  "version": 1,
  "ts": "2026-08-05T21:33:40.123+09:00",
  "source": "script.agent@1",
  "process_id": "proc_01J8ZQ...",
  "task_id": "task_01J8ZR...",
  "correlation_id": "job_01J8ZQ...",
  "causation_id": "evt_01J8ZP...",
  "payload": { }
}
```

| Trường | Ý nghĩa |
|---|---|
| `correlation_id` | gom tất cả event của cùng một Job |
| `causation_id` | event nào đã gây ra event này → dựng được cây nhân quả |
| `source` | ai phát (agent/kernel/provider/plugin), luôn có version |

## 2. Quy ước đặt tên

```
<domain>.<entity>.<action-quá-khứ>
```
- Domain: `kernel · workflow · agent · capability · provider · quality · memory · knowledge · cost · resource · permission · plugin · user`
- **Luôn dùng thì quá khứ**: `created`, `started`, `completed`, `failed`, `rejected`, `cancelled`. Event mô tả *việc đã xảy ra*, không phải mệnh lệnh.
- **Không bao giờ** đặt tên kiểu mệnh lệnh (`render_video`, `do_x`). Nếu bạn thấy mình muốn đặt tên như vậy → bạn đang muốn RPC, hãy dùng Workflow thay vì Event.

## 3. Danh mục

### 3.1 Kernel
| Event | Payload chính | Ghi chú |
|---|---|---|
| `kernel.job.received` | job_id, intent, inputs | điểm khởi đầu mọi thứ |
| `kernel.process.created` | pid, workflow_ref | |
| `kernel.process.started` | pid | |
| `kernel.process.progress` | pid, progress, message | throttle ≤ 1/giây |
| `kernel.process.paused` / `resumed` | pid, reason | |
| `kernel.process.checkpointed` | pid, seq | |
| `kernel.process.completed` | pid, duration_ms, cost, quality | |
| `kernel.process.failed` | pid, error_code, error_ctx | |
| `kernel.process.cancelled` | pid, by | |
| `kernel.task.scheduled` / `started` / `completed` / `failed` / `retried` | task_id, step_id, attempt | |
| `kernel.startup` / `kernel.shutdown` | version, uptime | |

### 3.2 Workflow & Decision
| Event | Payload |
|---|---|
| `workflow.selected` | workflow_ref, decision_id |
| `workflow.step.skipped` | step_id, condition |
| `workflow.loop.entered` | step_id, loop_count |
| `workflow.compensation.started` | steps[] |
| `decision.made` | decision_id, scope, chosen, rationale |

### 3.3 Agent
| Event | Payload |
|---|---|
| `agent.<name>.started` | agent_id, inputs_ref |
| `agent.<name>.thinking` | plan_summary |
| `agent.<name>.completed` | artifact_ids[], quality_score, duration_ms |
| `agent.<name>.failed` | error_code, retryable |
| `agent.<name>.rejected` | reason, feedback |

Ví dụ theo luồng video: `agent.planning.completed` → `plan.created` → Script Agent lắng nghe. Ngày mai thêm Thumbnail Agent chỉ cần `listens: [plan.created]`, **không sửa Planning Agent**.

### 3.4 Domain events (do plugin định nghĩa)
| Event | Ý nghĩa |
|---|---|
| `plan.created` | có kế hoạch nội dung |
| `script.created` / `script.revised` | |
| `image.batch.created` | |
| `voice.created` | |
| `video.rendered` | |

**Quy ước:** plugin chỉ được phát domain event mà nó khai báo trong manifest (`emits`). Kernel từ chối event không khai báo.

### 3.5 Capability & Provider
| Event | Payload |
|---|---|
| `capability.call.started` | call_id, capability, provider_id, estimate |
| `capability.call.completed` | call_id, latency_ms, cost, usage |
| `capability.call.failed` | call_id, error_code, will_fallback |
| `capability.fallback.triggered` | from_provider, to_provider, reason |
| `capability.cache.hit` | cache_key, saved_cost |
| `provider.health.changed` | provider_id, from, to |
| `provider.breaker.opened` / `closed` | provider_id, failures |
| `provider.stats.updated` | provider_id, task_class, quality_ewma |

### 3.6 Quality
| Event | Payload |
|---|---|
| `quality.review.started` | artifact_id, rubric |
| `quality.review.passed` | score, breakdown |
| `quality.review.rejected` | score, failed_criteria[], feedback |
| `quality.escalated.to_human` | artifact_id, reason |

### 3.7 Memory & Knowledge
| Event | Payload |
|---|---|
| `memory.item.written` | memory_id, tier, key |
| `memory.preference.learned` | key, value, confidence |
| `memory.consolidated` | from_tier, to_tier, n |
| `knowledge.node.created` | node_id, type, label |
| `knowledge.edge.created` | src, rel, dst, confidence |
| `knowledge.conflict.detected` | node_id, old, new |

### 3.8 Cost / Resource / Time
| Event | Payload |
|---|---|
| `cost.estimated` | process_id, amount, currency |
| `cost.recorded` | provider_id, amount |
| `cost.budget.warning` | pct_used |
| `cost.budget.exceeded` | budget_ref, action_taken |
| `resource.token.acquired` / `released` | token, holder |
| `resource.wait.started` | token, queue_len |
| `energy.deferred` | pid, reason: gpu_busy |
| `time.window.blocked` | pid, next_window_at |

### 3.9 Permission & Plugin
| Event | Payload |
|---|---|
| `permission.approval.requested` | approval_id, action, target, tier |
| `permission.approval.granted` / `denied` | approval_id, by |
| `permission.violation.blocked` | actor, action, target |
| `plugin.installed` / `removed` / `enabled` / `disabled` | plugin_id, version |
| `plugin.crashed` | plugin_id, error |

### 3.10 User
| Event | Payload |
|---|---|
| `user.feedback.given` | artifact_id, rating, comment |
| `user.correction.made` | artifact_id, before_ref, after_ref |

> `user.correction.made` là event **quý giá nhất** trong toàn hệ thống: nó là nguồn học Operational Knowledge trực tiếp từ bạn. Mọi UI phải làm cho việc sửa tay dễ dàng và luôn ghi lại.

## 4. Subscriber lõi (luôn chạy)

| Subscriber | Nghe | Làm gì |
|---|---|---|
| `TraceWriter` | `*` | dựng cây trace |
| `CostLedger` | `capability.call.completed`, `cost.*` | ghi sổ cái |
| `MemoryWriter` | `agent.*.completed`, `user.*`, `quality.*` | ghi memory ứng viên |
| `KnowledgeExtractor` | `agent.*.completed`, `user.correction.made` | trích node/edge |
| `StatsUpdater` | `capability.call.*`, `quality.*` | cập nhật `provider_stats` |
| `ProgressBroadcaster` | `kernel.*` | đẩy lên UI (SSE) |
| `ProjectLogger` | `*` có `process_id` | ghi `projects/<x>/logs/events.ndjson` |

## 5. Quy tắc vận hành Event Bus

1. **Ghi trước, phát sau.** Event vào SQLite trong transaction rồi mới dispatch.
2. **At-least-once.** Subscriber phải khử trùng lặp theo `event_id`.
3. **Không có event lớn.** Payload > 32KB → lưu thành Artifact, event chỉ chứa `artifact_id`.
4. **Không có PII thô trong payload** nếu có thể tham chiếu.
5. **Retention:** giữ toàn bộ event tối thiểu 2 năm; nén event `*.progress` sau 30 ngày (chỉ giữ mốc 0/25/50/75/100%).
6. **Replay an toàn:** subscriber phải chịu được replay (rebuild Memory/KG từ đầu là thao tác hợp lệ và được test).
