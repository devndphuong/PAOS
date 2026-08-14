-- Migration 005 — audit_log (doc 03 dong 244-246, doc 09 S9, doc 19 P-M2-5).
-- Cot lay NGUYEN VAN tu doc 03: id, actor, action, target, tier, approved_by,
-- detail_json, at. Append-only that su - enforce bang trigger chan UPDATE/DELETE
-- (doc 09 noi "enforce bang trigger SQLite", truoc lat nay chua co trigger nao).
-- Canh bao: khong go dau cham phay vao comment file nay - migration runner
-- (_apply_migrations, kernel/state/db.py) tach statement bang dau cham phay
-- tren toan file, khong hieu SQL hay comment (bai hoc that tu migration 004).
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT NOT NULL,
  target TEXT, tier TEXT, approved_by TEXT, detail_json TEXT, at TEXT NOT NULL);

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log la append-only, khong duoc UPDATE');
END;

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
  SELECT RAISE(ABORT, 'audit_log la append-only, khong duoc DELETE');
END;
