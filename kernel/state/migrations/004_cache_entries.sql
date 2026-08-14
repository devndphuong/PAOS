-- Migration 004 — cache_entries (doc 03 §5.2, doc 19 P-M2-4).
-- Sổ theo dõi cache theo nội dung (content-addressed): cache_key = sha256(capability
-- + version + normalized_input + provider_class). KHÔNG có process_id — cache là
-- toàn cục theo nội dung, không phải memoization theo từng process. File thật nằm
-- ở workspace/cache/<2 ký tự đầu cache_key>/<cache_key>.json — bảng này chỉ trỏ tới
-- đường dẫn đó, xoá cache/ trên đĩa không phải sửa bảng này (lookup() tự coi
-- record mồ côi là cache miss). Migration runner (_apply_migrations) tách statement
-- bằng dấu chấm phẩy trần trên toàn file — không được gõ ký tự đó trong comment ở đây.
CREATE TABLE cache_entries (
  cache_key TEXT PRIMARY KEY, capability_id TEXT NOT NULL, version INTEGER NOT NULL,
  provider_class TEXT NOT NULL, provider_id TEXT NOT NULL, output_path TEXT NOT NULL,
  bytes INTEGER, hit_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  last_hit_at TEXT);
