-- Migration 009 - decision_outcomes (doc 06 S1.3, doc 19 P-M6-1).
-- Cot dung NGUYEN VAN tu doc 06 - khong doi ten field, cung quy uoc migration 007/008.
-- Ghi 1 lan sau khi Process ket thuc (kernel.process.completed/failed) - Decision
-- Engine (apps/paosd/decision_engine.py) tra bang nay theo feature_hash TRUOC khi
-- cham diem candidate, day la "Operational Knowledge" o dang thuc dung nhat.
CREATE TABLE decision_outcomes (
  decision_id TEXT, feature_hash TEXT, chosen TEXT,
  succeeded INTEGER, quality REAL, cost REAL, duration_ms INTEGER, user_edited INTEGER, at TEXT);

-- Tra theo (feature_hash, chosen) la duong di NONG nhat: cham diem 1 candidate
-- can success_rate = AVG(succeeded) cho dung feature_hash + chosen do, moi lan
-- Decision Engine chon workflow.
CREATE INDEX idx_decision_outcomes_feature ON decision_outcomes(feature_hash, chosen);
