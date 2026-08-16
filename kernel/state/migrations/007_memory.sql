-- Migration 007 - memory_items + memory_vectors (doc 03 S3, doc 07, ADR-0015,
-- P-M5-1). Cot dung NGUYEN VAN tu doc 03 - khong doi ten field, tranh lech
-- giua tai lieu thiet ke va schema that.
--
-- memory_vectors la BANG THUONG (khong phai vec0 virtual table) - ADR-0015 da
-- chon sqlite-vec de tim kiem brute-force o quy mo du lieu 1 nguoi dung; vec0
-- virtual table (module vi du chinh thuc) chi ho tro "integer primary key",
-- xung dot voi quy uoc ID dang TEXT toan du an (ADR-0023). Dung bang thuong +
-- ham vo huong vec_distance_cosine() (sqlite-vec cung cap) la cach "thu cong"
-- chinh thuc sqlite-vec khuyen nghi cho dung truong hop brute-force nay - xem
-- addendum ADR-0015 (docs/15-adr-log.md).
CREATE TABLE memory_items (
  memory_id TEXT PRIMARY KEY, tier TEXT NOT NULL CHECK(tier IN ('L0','L1','L2','L3','L4')),
  scope_id TEXT, kind TEXT, key TEXT, content TEXT NOT NULL, meta_json TEXT,
  salience REAL NOT NULL DEFAULT 0.5, confidence REAL NOT NULL DEFAULT 0.7,
  source_json TEXT, expires_at TEXT, created_at TEXT NOT NULL,
  last_used_at TEXT, use_count INTEGER NOT NULL DEFAULT 0);

CREATE INDEX idx_mem_tier_key ON memory_items(tier, key);
CREATE INDEX idx_mem_tier_scope ON memory_items(tier, scope_id);

CREATE TABLE memory_vectors (
  memory_id TEXT PRIMARY KEY REFERENCES memory_items(memory_id),
  embedding BLOB NOT NULL, model TEXT NOT NULL, dim INTEGER NOT NULL);
