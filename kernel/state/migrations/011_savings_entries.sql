-- Migration 011 — savings_entries (doc 06 §3.4, doc 19 P-M7-3, ADR-0031).
-- kind: 'cache' (cache hit tránh 1 lượt gọi thật, avoided_provider_id = provider
-- đã tạo ra kết quả đang cache) | 'local' (candidate local được chọn trong khi
-- có ≥1 candidate cloud/hybrid ĐỦ ĐIỀU KIỆN cho cùng lượt gọi, avoided_provider_id
-- = candidate đó). amount luôn tính từ adapter.estimate() THẬT của
-- avoided_provider_id — không suy diễn từ giá trung bình/hardcode (ADR-0031,
-- cùng tinh thần ADR-0030 "không giả vờ có số"). Không FOREIGN KEY — cùng quy
-- ước migration 001-010, StateStore không bật enforce khoá ngoại.
CREATE TABLE savings_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT, process_id TEXT, task_id TEXT,
  capability TEXT NOT NULL, kind TEXT NOT NULL, avoided_provider_id TEXT,
  amount REAL NOT NULL, currency TEXT NOT NULL, at TEXT NOT NULL);

CREATE INDEX idx_savings_entries_at ON savings_entries(at);
