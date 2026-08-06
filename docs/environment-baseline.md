# Environment Baseline — Ngày 0

**Ghi lúc:** 2026-08-07 · Kết quả kiểm E1–E9 theo [doc 18 §2.2](18-day0-implementation-playbook.md), chạy trên máy phát triển chính.

| # | Kiểm | Kết quả | Ghi chú |
|---|---|---|---|
| E1 | Python ≥ 3.12 | ✅ 3.12.10 | Ban đầu **chưa cài** (chỉ có shortcut giả Microsoft Store) — đã cài qua `winget install Python.Python.3.12`. Cài tại `C:\Users\dacph\AppData\Local\Programs\Python\Python312\`. |
| E2 | SQLite ≥ 3.37 (WAL + `RETURNING`) | ✅ 3.49.1 | Đi kèm Python 3.12.10 vừa cài. |
| E3 | Đĩa trống nơi đặt `workspace/` | ✅ 346GB trống / 466GB (ổ D:) | Dư giả so với ngưỡng 20GB. |
| E4 | `ffmpeg` trong PATH | ❌ Chưa cài | Không chặn M0 (cần trước M3 — Video plugin). |
| E5 | Ollama cài đặt + trả lời | ❌ Chưa cài | Không chặn M0 — dùng `providers/stub/` (doc 18 §5 lát 4b). Cần cài trước khi test provider thật ở lát 4c. |
| E6 | GPU/VRAM khả dụng | ⚠ Có, nhưng yếu | NVIDIA GeForce GTX 1050, **4096 MiB VRAM**, driver 552.12. Ghi chú quan trọng: 4GB VRAM thấp hơn nhiều so với mức khuyến nghị cho model ảnh chất lượng cao (Flux full thường cần ≥ 12GB) — máy này gần như chắc chắn sẽ chạy `image.generate` ở chế độ degraded hoặc cần provider ảnh nhẹ/quantized. Đây là dữ liệu thật cho RSK-06 và bài kiểm LOC-05 đã thêm vào M3 exit criteria (doc 13), không phải giả định lý thuyết nữa. |
| E7 | Git đã cấu hình | ✅ | git 2.54.0 · `user.name=Nguyen Dac Phuong` · `user.email=devndphuong@gmail.com` |
| E8 | Mạng ra ngoài khả dụng | ⏭ Chưa kiểm | Không chặn M0 — kiểm khi cần test nhánh fallback cloud. |
| E9 | `workspace/` không nằm trong thư mục đồng bộ cloud | ✅ | OneDrive đồng bộ tại `C:\Users\dacph\OneDrive`; repo dự án nằm ở `D:\Project\PAOS` — ngoài hoàn toàn phạm vi OneDrive. An toàn để đặt `workspace/` tại `D:\Project\PAOS\workspace\`. |

## Kết luận

M0 làm được đầy đủ ngay bây giờ (E1/E2/E3/E7/E9 đạt — 5 mục bắt buộc). E4/E5 cần cài trước khi rời khỏi `providers/stub/` (lát 4c, doc 18 §5). E6 không chặn nhưng là rủi ro thật cần nhớ khi tới M3.

## Việc cần làm trước M3 (không chặn M0)

- [ ] Cài `ffmpeg` (E4)
- [ ] Cài Ollama + pull ít nhất 1 model (`qwen2.5-14b` theo ví dụ xuyên suốt tài liệu) (E5)
- [ ] Trước M3: quyết định biến thể "nhẹ" cho `image.generate` phù hợp 4GB VRAM (SDXL-Turbo/Flux-schnell quantized, hoặc chấp nhận CPU/cloud fallback nhiều hơn 20% mặc định ở RSK-05)
