-- Migration 006 — artifact_edits (doc 08 §5, doc 13 M4 exit criteria, P-M4-3).
-- Ghi lại edit_rate MỖI LẦN người dùng tự tay sửa 1 artifact — tín hiệu KHÁCH
-- QUAN duy nhất doc 08 §5 tin tưởng, không phải điểm rubric. original/edited
-- artifact_id đều trỏ vào bảng artifacts (migration 001) — edited_artifact_id
-- là bản MỚI với supersedes = original_artifact_id (ADR-0013), lần đầu tiên
-- field supersedes thật sự được một caller điền (write_artifact() của agent
-- luôn để None). Không FOREIGN KEY — cùng quy ước migration 001-005, StateStore
-- không bật enforce khoá ngoại (xem kernel/state/db.py).
CREATE TABLE artifact_edits (
  edit_id TEXT PRIMARY KEY, process_id TEXT NOT NULL,
  original_artifact_id TEXT NOT NULL, edited_artifact_id TEXT NOT NULL,
  edit_rate REAL NOT NULL, created_at TEXT NOT NULL);

CREATE INDEX idx_artifact_edits_original ON artifact_edits(original_artifact_id);
