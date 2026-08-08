# Backlog

Ý tưởng chưa chín, nợ kỹ thuật có điều kiện trả — không phải TODO ngẫu nhiên. Mỗi mục ghi rõ: phát sinh khi nào, điều kiện trả nợ là gì.

## Nợ kỹ thuật

### BL-001 · EventBus không tự retry/backoff khi subscriber lỗi
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** `_deliver_one()` (`kernel/events/bus.py`) bắt lỗi subscriber, ghi `event_deliveries.state="failed"` rồi dừng — không tự thử lại, không backoff. Event chỉ được giao lại nếu daemon restart (catch-up scan `EventBus.start()`).
**Vì sao chấp nhận tạm thời:** M0 phạm vi "Process 1 task", một subscriber (`project_logger`), lỗi subscriber ở M0 gần như luôn là lỗi lập trình (đáng thấy ngay) chứ không phải lỗi thoáng qua cần retry.
**Điều kiện trả nợ:** M1 (P-M1-4, doc 19) — thêm số lần thử + backoff có giới hạn trước khi coi là failed vĩnh viễn.

### BL-002 · Không có Dead Letter Queue + lệnh replay cho event failed vĩnh viễn
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** Event ở trạng thái `event_deliveries.state="failed"` nằm im trong bảng, không ai xem lại hay chủ động phát lại được — chỉ có thể query tay qua SQL.
**Vì sao chấp nhận tạm thời:** gắn liền BL-001 — chưa có retry thật thì DLQ cũng chưa có gì để phân biệt với "failed" thường.
**Điều kiện trả nợ:** M1 (P-M1-4, doc 19) — `paosctl events replay --from --to --to-subscriber` (đã nhắc ở doc 19 dòng 617) + view/lệnh liệt kê event trong DLQ.

### BL-003 · ProjectLogger ghi log toàn cục thay vì theo từng project
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** `make_project_logger()` (`kernel/events/bus.py`) ghi mọi event vào `workspace/.paos/logs/events.ndjson` — một file chung cho toàn hệ thống, không tách theo `projects/<x>/logs/events.ndjson` như doc 03 §4 mô tả.
**Vì sao chấp nhận tạm thời:** Process/Project (lát cắt 3+) chưa tồn tại lúc viết lát cắt 2 — chưa có gì để tách theo. Trừu tượng hóa sớm ở đây sẽ đoán sai hình dạng.
**Điều kiện trả nợ:** khi `process_id` → `project` mapping thật tồn tại. **Cập nhật (nghiệm thu M0, 2026-08-08):** M0 lát cắt 3 (Process SM) đã xong nhưng khái niệm Project vẫn CHƯA tồn tại (hoãn tới M3/M6, xem `workspace/artifacts/<process_id>/` — quy ước tạm ở `sdk/agent.py`) — điều kiện trả nợ vẫn chưa đạt, nợ này còn treo tới M3/M6, không phải M1.

---

## Mục chưa phân loại

*(trống)*
