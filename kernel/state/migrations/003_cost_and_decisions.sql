-- Migration 003 — cost_entries + decisions (doc 03 §"Artifact & Cost & Decision").
-- Chừa cột, đừng chừa code (doc 18 §10): 2 bảng này có trong domain model gốc
-- từ Ngày 0 nhưng bị BỎ SÓT khỏi danh sách "chừa cột ở M0" — phát hiện lúc
-- soát idempotency/error taxonomy ở M1-6 (doc 19 P-M1-5 cũ). Cost Engine thật
-- là M7, Decision Record đầy đủ gắn với Router thật là M2 — schema có mặt từ
-- đây, KHÔNG có logic nào ghi vào 2 bảng này ở M1.
CREATE TABLE cost_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, process_id TEXT, task_id TEXT,
  provider_id TEXT, capability TEXT, unit TEXT, qty REAL,
  amount REAL NOT NULL, currency TEXT NOT NULL, estimated INTEGER DEFAULT 0, at TEXT);

CREATE TABLE decisions (
  decision_id TEXT PRIMARY KEY, process_id TEXT, scope TEXT,
  question TEXT, candidates_json TEXT, chosen TEXT, rationale TEXT,
  policy_version TEXT, inputs_hash TEXT, created_at TEXT);
