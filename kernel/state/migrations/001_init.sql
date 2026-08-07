-- Migration 001 — nền tảng M0 (doc 19 P-M0-1). Cột lấy NGUYÊN VĂN từ doc 03 §3.
-- Bảng checkpoints và cột tasks.attempts/idempotency_key có mặt nhưng chưa có
-- logic dùng ở M0 — chủ đích, xem doc 18 §10 "chừa cột, đừng chừa code".

CREATE TABLE counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0
);
INSERT INTO counters(name, value) VALUES ('pid', 1000);

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

CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY, process_id TEXT, task_id TEXT, type TEXT,
  path TEXT NOT NULL, mime TEXT, sha256 TEXT, bytes INTEGER,
  produced_by_json TEXT, quality_json TEXT, supersedes TEXT, created_at TEXT);
