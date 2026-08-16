-- Migration 008 - kg_nodes + kg_edges (doc 03 S3, doc 07 S4, P-M5-3).
-- Cot dung NGUYEN VAN tu doc 03 - khong doi ten field, tranh lech giua tai
-- lieu thiet ke va schema that (cung quy uoc migration 007).
CREATE TABLE kg_nodes (
  node_id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL,
  aliases_json TEXT, props_json TEXT, confidence REAL, first_seen TEXT, last_seen TEXT);

CREATE TABLE kg_edges (
  edge_id TEXT PRIMARY KEY, src TEXT NOT NULL REFERENCES kg_nodes,
  dst TEXT NOT NULL REFERENCES kg_nodes, rel TEXT NOT NULL, weight REAL DEFAULT 1.0,
  confidence REAL, provenance_json TEXT, created_at TEXT, invalidated_at TEXT);
CREATE INDEX idx_kg_src ON kg_edges(src, rel);

-- Index phu: walk 2-hop (doc 07 S3 buoc 2, BL-013) can tra CA hai chieu -
-- idx_kg_src (co san tu doc 03) chi phu chieu src->rel. dst khong co index
-- rieng trong doc 03 nhung can that de tranh full scan khi do la Knowledge
-- Graph tich luy nhieu nam (doc 07 S4.3) - khong doi hop dong cot, chi them
-- index, an toan hoan toan voi doc 03 S3 ("hop dong").
CREATE INDEX idx_kg_dst ON kg_edges(dst, rel);

-- Tra theo (type, label) khong phan biet hoa/thuong la duong di NONG nhat
-- (moi lan trich xuat entity phai kiem trung lap - doc 07 S4.4 "khu trung
-- lap entity") - index tren label tho (khong LOWER()) van giup SQLite loc
-- nhanh truoc khi so sanh case-insensitive tai tang ung dung.
CREATE INDEX idx_kg_node_type_label ON kg_nodes(type, label);
