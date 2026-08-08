# Backlog

Ý tưởng chưa chín, nợ kỹ thuật có điều kiện trả — không phải TODO ngẫu nhiên. Mỗi mục ghi rõ: phát sinh khi nào, điều kiện trả nợ là gì.

## Nợ kỹ thuật

### BL-003 · ProjectLogger ghi log toàn cục thay vì theo từng project
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** `make_project_logger()` (`kernel/events/bus.py`) ghi mọi event vào `workspace/.paos/logs/events.ndjson` — một file chung cho toàn hệ thống, không tách theo `projects/<x>/logs/events.ndjson` như doc 03 §4 mô tả.
**Vì sao chấp nhận tạm thời:** Process/Project (lát cắt 3+) chưa tồn tại lúc viết lát cắt 2 — chưa có gì để tách theo. Trừu tượng hóa sớm ở đây sẽ đoán sai hình dạng.
**Điều kiện trả nợ:** khi M0 lát cắt 3 (Process state machine) xong và có `process_id` → `project` mapping thật, sửa `make_project_logger` nhận `process_id`, tra thư mục project tương ứng, ghi đúng `projects/<x>/logs/events.ndjson` theo doc 03 §4.

---

## Mục chưa phân loại

*(trống)*
