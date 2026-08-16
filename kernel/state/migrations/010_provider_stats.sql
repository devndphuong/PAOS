-- Migration 010 - provider_stats (doc 06 S2.2/S2.4, doc 19 P-M6-2)
-- Khoa (provider_id, task_class) - Q_hat la "chat luong chuan hoa 0-1 cho task_class
-- CU THE" (doc 06), R (do tin cay) la success_rate GAN DAY cung tinh o do rong nay,
-- khong tach rieng theo capability. quality_n dem so lan quan sat that (rubric score
-- hoac artifact.edited) - Router chi dung quality_ewma khi quality_n >= 5, duoi
-- nguong do dung quality_hint trong provider.yaml (doc 06 S2.2).
-- Migration runner tach statement bang dau cham phay tran tren toan file - khong go
-- ky tu do trong comment o day.
CREATE TABLE provider_stats (
  provider_id TEXT NOT NULL, task_class TEXT NOT NULL,
  quality_ewma REAL, quality_n INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, fail_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT, PRIMARY KEY (provider_id, task_class));
