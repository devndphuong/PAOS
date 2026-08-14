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

### BL-004 · Ollama chưa an toàn để `Registry.load_adapter()` nạp — 2 nợ liên quan
**Phát sinh:** M0 lát cắt 4c (Ollama adapter) + phát hiện lại khi dựng Conformance Suite (P-M2-2, 2026-08-14).
**Hiện trạng:**
1. `providers/ollama/provider.yaml` CỐ Ý chưa khai `adapter:`. Đã thử thêm khi dựng Conformance Suite và xác nhận THẬT: `apps/paosd/runner.py::_make_call_capability()` (dòng 326-358) chỉ thử LẦN LƯỢT provider theo `Registry.providers_for()` và dừng ở provider ĐẦU TIÊN **nạp được** — chưa có fallback thật theo kết quả `invoke()` (đó là Router P-M2-3). Nếu Ollama nạp được, Registry có thể chọn nó thay vì `stub.deterministic` bất cứ khi nào thứ tự scan thư mục trả về ollama trước (không đảm bảo) — vỡ 18 test golden-path (`test_scheduler`, `test_resume`, `test_runner`, `test_determinism`, `test_cli`...) vì `adapter_overrides={"stub.deterministic": ...}` của chúng bị bỏ qua, code gọi thật ra `localhost:11434` (máy chưa cài Ollama).
2. `OllamaAdapter.cancel()` (`providers/ollama/adapter.py`) là no-op tuyệt đối — không dừng request `httpx` đang chạy, khác `StubAdapter` (đã có cancel thật từ P-M2-2).
**Vì sao chấp nhận tạm thời:** cả 2 đều cần Router thật (P-M2-3: fallback theo kết quả `invoke()`, không chỉ theo `load()`) trước khi an toàn bật `adapter:` cho Ollama. Conformance Suite (`tests/contract/test_capability_text_generate.py`) vẫn tự discover `ollama.qwen2.5-14b` qua `providers_for()` (doc 18 R24), nhưng SKIP rõ ràng các chỉ tiêu cần `Registry.load_adapter()` (`_load_adapter_or_skip`) — chỉ `test_error_simulation` chạy được vì dùng `OllamaAdapter` trực tiếp, không qua Registry. `tests/providers/ollama/test_ollama_adapter.py::test_load_adapter_via_registry_fails_by_design` khoá lại chủ đích này.
**Điều kiện trả nợ:** P-M2-3 (Router thật) — khi đó thêm `adapter:` vào provider.yaml an toàn, sửa `cancel()` thật, rồi gỡ các skip trong Conformance Suite + test khoá chủ đích ở trên.

---

## Mục chưa phân loại

*(trống)*
