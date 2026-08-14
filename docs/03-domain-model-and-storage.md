# 03 — Domain Model & Storage

**Trạng thái:** v1.0 · **Là hợp đồng dữ liệu, thay đổi cần migration**

---

## 1. Các thực thể lõi

```
Job (ý định của người dùng)
 └── Process (một lần thực thi Job, có PID)
      └── Task (một đơn vị công việc = 1 lần gọi Agent hoặc Capability)
           └── Attempt (một lần thử; retry tạo Attempt mới)
                └── CapabilityCall (lần gọi thực tế xuống Provider)

Artifact  — mọi file/kết quả sinh ra, bất biến, có hash
Event     — sự kiện bất biến, append-only
DecisionRecord — vì sao hệ thống chọn phương án đó
MemoryItem / KGNode / KGEdge — tri thức tích lũy
CostEntry — sổ cái chi phí
```

### 1.1 Quy ước ID
ULID có tiền tố, sắp xếp được theo thời gian, dễ đọc trong log:

```
job_01J8ZQ...   proc_01J8ZQ...   task_01J8ZQ...   evt_01J8ZQ...
art_01J8ZQ...   dec_01J8ZQ...    call_01J8ZQ...   mem_01J8ZQ...
```

Ngoài ra Process có **PID** dạng số nguyên tăng dần (1001, 1002…) để con người gọi cho tiện — giống Windows/Linux.

## 2. Định nghĩa dữ liệu (JSON)

### 2.1 JobSpec
```json
{
  "schema_version": 1,
  "job_id": "job_01J8ZQ...",
  "intent": "video.create",
  "raw_request": "Làm video 60s về MongoDB từ file này",
  "inputs": [{"type": "file", "path": "workspace/inbox/mongodb.pdf", "mime": "application/pdf"}],
  "constraints": {
    "deadline": null,
    "budget": {"max": 20, "currency": "JPY"},
    "privacy": "private",
    "offline_only": false
  },
  "preferences_snapshot": {
    "video.duration_sec": 75, "tone": "professional", "voice.gender": "female"
  },
  "priority": 5,
  "created_at": "2026-08-05T21:30:00+09:00"
}
```
`preferences_snapshot` được **chụp lại tại thời điểm tạo Job** để Job cũ luôn tái lập được, kể cả khi sở thích thay đổi sau này.

### 2.2 Process
```json
{
  "schema_version": 1,
  "process_id": "proc_01J8ZQ...",
  "pid": 1001,
  "job_id": "job_01J8ZQ...",
  "name": "RenderVideo: MongoDB",
  "workflow_ref": "video.from_pdf@2",
  "state": "RUNNING",
  "progress": 0.45,
  "current_tasks": ["task_01J8ZR..."],
  "started_at": "2026-08-05T21:30:12+09:00",
  "checkpoint_seq": 7,
  "resource_holdings": ["gpu:1"],
  "cost_so_far": {"amount": 0, "currency": "JPY"},
  "error": null
}
```

### 2.3 Task
```json
{
  "schema_version": 1,
  "task_id": "task_01J8ZR...",
  "process_id": "proc_01J8ZQ...",
  "step_id": "generate_images",
  "kind": "agent",                
  "ref": "image.agent@1",
  "depends_on": ["write_script"],
  "inputs_ref": {"script": "art_01J8ZS..."},
  "resources": ["gpu:1"],
  "retry_policy": {"max": 3, "backoff": "exp", "base_ms": 2000},
  "idempotency_key": "proc_01J8ZQ:generate_images:v1:sha256(inputs)",
  "state": "RUNNING",
  "attempts": 1,
  "quality_score": null
}
```
`kind` ∈ `agent | capability | human | system`.

### 2.4 Artifact
```json
{
  "schema_version": 1,
  "artifact_id": "art_01J8ZS...",
  "process_id": "proc_01J8ZQ...",
  "task_id": "task_01J8ZR...",
  "type": "script",              
  "path": "projects/video_mongodb/script.json",
  "mime": "application/json",
  "sha256": "9f2c...",
  "bytes": 8123,
  "produced_by": {"agent": "script.agent@1", "provider": "ollama.qwen2.5-14b"},
  "quality": {"score": 91, "rubric": "script.rubric@1"},
  "supersedes": "art_01J8ZP...",
  "created_at": "2026-08-05T21:33:40+09:00"
}
```
**Artifact là bất biến.** Sửa = tạo artifact mới có `supersedes`. Nhờ vậy luôn truy vết được lịch sử và so sánh được các phiên bản.

### 2.5 DecisionRecord
```json
{
  "schema_version": 1,
  "decision_id": "dec_01J8ZT...",
  "process_id": "proc_01J8ZQ...",
  "scope": "provider_selection",  
  "question": "capability=text.generate, task_class=script_writing_vi",
  "candidates": [
    {"id": "ollama.qwen2.5-14b", "score": 0.82, "quality": 82, "cost": 0,  "latency_ms": 14000, "eligible": true},
    {"id": "openai.gpt",         "score": 0.79, "quality": 96, "cost": 5,  "latency_ms": 4000,  "eligible": true},
    {"id": "anthropic.claude",   "score": 0.00, "eligible": false, "reason": "BUDGET_EXCEEDED"}
  ],
  "chosen": "ollama.qwen2.5-14b",
  "rationale": "Chất lượng offline vượt ngưỡng 80 cho task_class này; chi phí 0 thắng ở trọng số cost=0.35",
  "policy_version": "routing@3",
  "inputs_hash": "sha256:...",
  "created_at": "..."
}
```
`scope` ∈ `workflow_selection | provider_selection | retry | escalation | scheduling | budget | cache_hit` (`cache_hit` thêm ở P-M2-4 — trúng cache, không có candidate nào được xét, `chosen` = provider_id đã tạo ra kết quả gốc).

## 3. Schema SQLite

File: `workspace/.paos/state.db` (WAL). Toàn bộ bảng dưới đây là **hợp đồng**.

```sql
-- === Kernel ===
CREATE TABLE jobs (
  job_id TEXT PRIMARY KEY, intent TEXT NOT NULL, spec_json TEXT NOT NULL,
  priority INTEGER DEFAULT 5, created_at TEXT NOT NULL, schema_version INTEGER NOT NULL);

CREATE TABLE processes (
  process_id TEXT PRIMARY KEY, pid INTEGER UNIQUE, job_id TEXT NOT NULL REFERENCES jobs,
  name TEXT, workflow_ref TEXT NOT NULL, state TEXT NOT NULL, progress REAL DEFAULT 0,
  checkpoint_seq INTEGER DEFAULT 0, started_at TEXT, ended_at TEXT,
  error_code TEXT, error_json TEXT, schema_version INTEGER NOT NULL);
CREATE INDEX idx_proc_state ON processes(state);

CREATE TABLE process_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, process_id TEXT NOT NULL REFERENCES processes,
  from_state TEXT, to_state TEXT NOT NULL, reason TEXT, at TEXT NOT NULL);

CREATE TABLE tasks (
  task_id TEXT PRIMARY KEY, process_id TEXT NOT NULL REFERENCES processes,
  step_id TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL,
  depends_on_json TEXT, inputs_json TEXT, state TEXT NOT NULL,
  attempts INTEGER DEFAULT 0, idempotency_key TEXT UNIQUE,
  quality_score REAL, started_at TEXT, ended_at TEXT, error_code TEXT);
CREATE INDEX idx_task_proc ON tasks(process_id, state);

CREATE TABLE checkpoints (
  process_id TEXT NOT NULL REFERENCES processes, seq INTEGER NOT NULL,
  state_json TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(process_id, seq));

-- === Event Bus ===
CREATE TABLE events (
  event_id TEXT PRIMARY KEY, seq INTEGER, type TEXT NOT NULL, version INTEGER NOT NULL,
  ts TEXT NOT NULL, source TEXT NOT NULL, process_id TEXT, task_id TEXT,
  correlation_id TEXT, causation_id TEXT, payload_json TEXT NOT NULL);
CREATE INDEX idx_events_type_ts ON events(type, ts);
CREATE INDEX idx_events_proc ON events(process_id, ts);

CREATE TABLE event_deliveries (
  event_id TEXT NOT NULL REFERENCES events, subscriber TEXT NOT NULL,
  state TEXT NOT NULL, attempts INTEGER DEFAULT 0, last_error TEXT,
  PRIMARY KEY(event_id, subscriber));

CREATE TABLE dead_letters (
  event_id TEXT, subscriber TEXT, error TEXT, at TEXT);

-- === Registry ===
CREATE TABLE providers (
  provider_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, enabled INTEGER DEFAULT 1,
  breaker_state TEXT DEFAULT 'CLOSED', breaker_until TEXT, health TEXT DEFAULT 'UNKNOWN',
  last_checked TEXT);

CREATE TABLE provider_stats (           -- OPERATIONAL KNOWLEDGE: tài sản dài hạn
  provider_id TEXT NOT NULL, capability TEXT NOT NULL, task_class TEXT NOT NULL,
  n INTEGER DEFAULT 0, success_rate REAL, quality_ewma REAL,
  latency_p50_ms REAL, latency_p95_ms REAL, cost_avg REAL, updated_at TEXT,
  PRIMARY KEY(provider_id, capability, task_class));

CREATE TABLE plugins (
  plugin_id TEXT PRIMARY KEY, version TEXT, manifest_json TEXT,
  permissions_json TEXT, installed_at TEXT, enabled INTEGER DEFAULT 1);

-- === Artifact & Cost & Decision ===
CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY, process_id TEXT, task_id TEXT, type TEXT,
  path TEXT NOT NULL, mime TEXT, sha256 TEXT, bytes INTEGER,
  produced_by_json TEXT, quality_json TEXT, supersedes TEXT, created_at TEXT);

CREATE TABLE cost_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, process_id TEXT, task_id TEXT,
  provider_id TEXT, capability TEXT, unit TEXT, qty REAL,
  amount REAL NOT NULL, currency TEXT NOT NULL, estimated INTEGER DEFAULT 0, at TEXT);

CREATE TABLE decisions (
  decision_id TEXT PRIMARY KEY, process_id TEXT, scope TEXT,
  question TEXT, candidates_json TEXT, chosen TEXT, rationale TEXT,
  policy_version TEXT, inputs_hash TEXT, created_at TEXT);

-- === Memory & Knowledge ===
CREATE TABLE memory_items (
  memory_id TEXT PRIMARY KEY, tier TEXT NOT NULL, scope_id TEXT,
  kind TEXT, key TEXT, content TEXT NOT NULL, meta_json TEXT,
  salience REAL DEFAULT 0.5, confidence REAL DEFAULT 0.7,
  source_json TEXT, expires_at TEXT, created_at TEXT, last_used_at TEXT, use_count INTEGER DEFAULT 0);
CREATE INDEX idx_mem_tier_key ON memory_items(tier, key);

CREATE TABLE memory_vectors (           -- sqlite-vec
  memory_id TEXT PRIMARY KEY REFERENCES memory_items, embedding BLOB, model TEXT, dim INTEGER);

CREATE TABLE kg_nodes (
  node_id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,
  aliases_json TEXT, props_json TEXT, confidence REAL, first_seen TEXT, last_seen TEXT);

CREATE TABLE kg_edges (
  edge_id TEXT PRIMARY KEY, src TEXT NOT NULL REFERENCES kg_nodes,
  dst TEXT NOT NULL REFERENCES kg_nodes, rel TEXT NOT NULL, weight REAL DEFAULT 1.0,
  confidence REAL, provenance_json TEXT, created_at TEXT, invalidated_at TEXT);
CREATE INDEX idx_kg_src ON kg_edges(src, rel);

-- === Audit & Permission ===
CREATE TABLE audit_log (               -- append-only, không bao giờ UPDATE/DELETE
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT NOT NULL,
  target TEXT, tier TEXT, approved_by TEXT, detail_json TEXT, at TEXT NOT NULL);

CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
```

## 4. Filesystem — Workspace

**Luật:** mọi thứ nằm trong Project. Không có dữ liệu ẩn ngoài `.paos/`.

```
workspace/
├── .paos/
│   ├── state.db            # SQLite
│   ├── config.yaml         # cấu hình người dùng
│   ├── policies/           # routing.yaml, budget.yaml, time.yaml, permission.yaml
│   ├── locks/
│   └── backups/            # snapshot state.db theo ngày
├── inbox/                  # thả file vào đây để PAOS tự nhận
├── projects/
│   └── video_mongodb/
│       ├── project.json        # metadata, trạng thái, liên kết Job
│       ├── job.json            # JobSpec gốc
│       ├── workflow.json       # DAG đã được resolve (đóng băng để tái lập)
│       ├── script.json
│       ├── prompt/             # mọi prompt đã dùng, có version
│       ├── image/
│       ├── voice/
│       ├── subtitle/
│       ├── output/
│       │   └── video.mp4
│       ├── cache/              # xoá được, không ảnh hưởng tính đúng
│       ├── logs/
│       │   ├── events.ndjson   # bản sao event của riêng project
│       │   └── trace.json
│       └── review/             # báo cáo Review Agent, điểm số, feedback
├── knowledge/              # tri thức cá nhân xuất ra dạng đọc được
│   ├── graph.jsonld
│   ├── notes/
│   └── operational/        # playbook, template thắng, lỗi thường gặp
├── models/                 # trọng số model local (không commit)
├── plugins/
├── cache/                  # cache toàn cục (theo hash nội dung)
├── output/                 # nơi xuất bản cuối cùng
└── trash/                  # xóa = chuyển vào đây, dọn sau N ngày
    └── 2026-08-05/
```

**Nguyên tắc `cache/`:** xóa toàn bộ `cache/` bất cứ lúc nào không được làm sai kết quả, chỉ làm chậm. Đây là bài kiểm tra bắt buộc trong CI.

## 5. Nguyên tắc dữ liệu

1. **Append-only ở nơi quan trọng:** `events`, `audit_log`, `artifacts` không bao giờ UPDATE.
2. **Content-addressed cache:** key = `sha256(capability + version + normalized_input + provider_class)`. Nhờ vậy chạy lại Job giống hệt = 0 chi phí.
3. **Idempotency:** mỗi Task có `idempotency_key` duy nhất; chạy lại không tạo bản ghi chi phí trùng.
4. **Backup:** trước mỗi migration và mỗi ngày → `.paos/backups/state-YYYYMMDD.db` (giữ 14 bản).
5. **Export:** `paosctl export --project X --format bundle` → 1 thư mục zip đầy đủ mở được bằng tay, không cần PAOS.
6. **Migration:** script đánh số tăng dần trong `kernel/state/migrations/`, chạy tự động khi khởi động, có `up` bắt buộc và `down` khuyến khích.
