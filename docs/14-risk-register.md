# 14 — Risk Register

**Trạng thái:** v1.0 · Rà soát lại mỗi cuối milestone.

Thang điểm: **P** = xác suất (1–5), **I** = tác động (1–5), **R** = P×I.

---

## Rủi ro nghiêm trọng (R ≥ 15)

### RSK-01 · Over-engineering — xây OS mà không bao giờ dùng được · P4 I5 **R20**
Kiến trúc 10 tầng rất dễ dẫn tới 6 tháng code hạ tầng mà chưa làm nổi một video.
**Giảm thiểu:** M0 Walking Skeleton bắt buộc chạy end-to-end trong 2 tuần · mỗi milestone phải có sản phẩm dùng được · quy tắc "không viết trừu tượng cho trường hợp thứ hai chưa tồn tại" (chỉ trừu tượng hóa khi đã có 2 ca dùng thật).
**Dấu hiệu cảnh báo:** 3 tuần liên tiếp không có Job nào chạy thật.

### RSK-02 · Một người làm, kiệt sức hoặc gián đoạn · P4 I5 **R20**
Dự án 12 tháng của một người, không có ai tiếp quản.
**Giảm thiểu:** tài liệu này chính là biện pháp chính · commit nhỏ, luôn ở trạng thái chạy được · mỗi milestone độc lập có giá trị · chấp nhận dừng 1 tháng mà quay lại vẫn hiểu được code (nhờ ADR + docs).

### RSK-03 · Kernel bị nhiễm bẩn dần bởi logic AI · P4 I5 **R20**
Một hôm bạn "chỉ thêm tạm" một `if provider == 'openai'` vào Scheduler. Sau 2 năm Kernel không tách được nữa và toàn bộ mục tiêu 10 năm sụp đổ.
**Giảm thiểu:** CI check tự động (MNT-01) chạy mỗi PR · job CI xóa `providers/` + `agents/` rồi build Kernel · từ chối PR vi phạm P1/P3/P4 kể cả khi chạy đúng.

### RSK-04 · Scope creep — mỗi ý tưởng mới lại thêm một Engine · P5 I3 **R15**
Vision rất rộng (Energy, Time, Ethics, Marketplace...). Rất dễ thêm mãi.
**Giảm thiểu:** Anti-goals ở doc 00 §6 · mọi tính năng mới phải trả lời 4 câu hỏi ở README §3 · ý tưởng mới vào `docs/backlog.md`, không vào code.

---

## Rủi ro cao (R 10–14)

### RSK-05 · Chất lượng model local không đủ dùng · P3 I4 **R12**
Nếu Qwen 14B viết script quá tệ, toàn bộ lời hứa "chi phí 0" sụp.
**Giảm thiểu:** thiết kế sẵn tier fallback lên cloud · ngân sách nhỏ nhưng khác 0 · eval suite đo khách quan thay vì cảm tính · chấp nhận 20% job dùng cloud là thành công, không phải thất bại.

### RSK-06 · Phần cứng không đủ (không GPU) · P3 I4 **R12**
**Giảm thiểu:** chế độ degraded bắt buộc (LOC-05) · Energy Engine xếp hàng thay vì crash · workflow có biến thể "nhẹ" cho máy yếu. **Cập nhật (M3, P-M3-5, 2026-08-15):** LOC-05 lần đầu có triển khai thật + test đối kháng — `Step.required: false` (`kernel/workflow/spec.py`) cho sub-step trong 1 group `parallel`, `image.agent@1` khai `required: false` trong `workflows/video.plan_and_script/3/`; đã xác nhận qua test `test_uc1_degraded_mode_when_image_generate_has_no_gpu` (giả lập `image.generate` báo `RESOURCE_EXHAUSTED`) → Process vẫn `SUCCEEDED`, `video.rendered.degraded=true`, không crash, không lỗi âm thầm (`kernel.task.failed` + `workflow.step.skipped` ghi lại lý do thật). Energy Engine xếp hàng (M7) vẫn chưa làm — chỉ phần "không crash" của RSK-06 được giải quyết ở M3.

### RSK-07 · Provider bên ngoài đổi API / ngừng dịch vụ · P4 I3 **R12**
**Giảm thiểu:** đây chính là lý do tồn tại của Capability Layer · adapter mỏng ≤ 200 dòng · conformance suite phát hiện sớm khi API đổi · luôn có ít nhất 1 provider local cho mọi capability lõi.

### RSK-08 · Hỏng dữ liệu / mất Workspace · P2 I5 **R10**
**Giảm thiểu:** WAL + transaction · backup hàng ngày + trước migration · `paosctl doctor` kiểm tra toàn vẹn · Trash thay vì xóa · khuyến nghị mạnh: workspace nằm trong thư mục có backup ngoài (rsync/cloud sync do bạn tự chọn).

### RSK-09 · Vòng lặp tự sửa gây cháy chi phí/thời gian · P3 I4 **R12**
**Giảm thiểu:** `max_loops` cứng · yêu cầu cải thiện ≥ 5 điểm giữa 2 vòng · ngân sách retry riêng ≤ 30% · budget 3 tầng với hành động tự động khi vượt.

---

## Rủi ro trung bình (R 5–9)

### RSK-10 · LLM-as-judge chấm điểm không đáng tin · P4 I2 **R8**
**Giảm thiểu:** ưu tiên kiểm tất định · judge khác generator · hiệu chuẩn tay 20 mẫu/tháng · dùng `edit_rate` làm chỉ số thật.

### RSK-11 · Memory tích lũy rác, càng dùng càng tệ · P3 I3 **R9**
**Giảm thiểu:** chỉ ghi L3 từ hành vi quan sát được · ngưỡng confidence · consolidation có kiểm duyệt · `paosctl memory review` định kỳ · nút quên luôn sẵn.

### RSK-12 · Prompt injection từ tài liệu đầu vào · P3 I3 **R9**
**Giảm thiểu:** luật Data ≠ Instruction (doc 09 §4) · không hành động nào sinh ra từ nội dung · Permission Guard không có đường tắt.

### RSK-13 · Plugin bên thứ ba gây hại · P2 I4 **R8**
**Giảm thiểu:** sandbox subprocess · quyền tối thiểu khai báo · duyệt tay khi cài · tự disable khi vi phạm · v1 chỉ cài plugin do chính bạn viết.

### RSK-14 · Schema thay đổi làm hỏng dữ liệu cũ · P3 I3 **R9**
**Giảm thiểu:** `schema_version` ở mọi bản ghi · migration có thứ tự + backup trước · test migration với DB thật của bản trước · deprecation 2 phiên bản.

### RSK-15 · Debug hệ thống bất định rất khó · P4 I2 **R8**
**Giảm thiểu:** `PAOS_MODE=deterministic` với fixture · seed cố định · trace đầy đủ · record & replay mọi capability call trong chế độ debug.

### RSK-16 · SQLite trở thành nút thắt · P2 I3 **R6**
**Giảm thiểu:** WAL · chỉ 1 writer (Kernel) · index đúng · rollup event progress · nếu vượt SCL-01 thì tách event sang file riêng — nhưng chỉ khi **đo được** là nghẽn, không tối ưu sớm.

---

## Rủi ro thấp nhưng cần theo dõi

| ID | Rủi ro | R | Giảm thiểu |
|---|---|---|---|
| RSK-17 | Python quá chậm cho Kernel | 4 | Kernel chỉ điều phối, việc nặng nằm ở provider; nếu nghẽn thật thì viết lại module đó bằng Rust — contract cho phép |
| RSK-18 | Phụ thuộc thư viện bị bỏ rơi | 6 | Ưu tiên thư viện chuẩn; mỗi phụ thuộc ngoài phải có phương án thay thế ghi trong ADR |
| RSK-19 | Chi phí lưu trữ artifact phình to | 4 | Chính sách dọn `cache/`, nén artifact cũ, báo cáo dung lượng trong `doctor` |
| RSK-20 | Mất động lực vì không thấy tiến triển | 9 | Báo cáo tuần tự động "PAOS đã học được gì" — biến tri thức tích lũy thành thứ nhìn thấy được |
| RSK-21 | ~~Runner M0 chạy Agent đồng bộ ngay trong `dispatch()`~~ — **ĐÃ GIẢI QUYẾT ở M1-2/M1-3a** (2026-08-09): `POST /v1/jobs` chỉ đảm bảo `QUEUED` (ADR-0026), agent chạy nền qua `worker_loop()`, N job song song thật qua `asyncio.Semaphore` | ~~9~~ 0 | Không còn cần theo dõi |
| RSK-22 | EventBus catch-up (REL-01) gọi lại subscriber khi crash giữa lúc xử lý — MỌI subscriber tương lai (M5 Memory Writer, M6 KG builder, ...) phải idempotent hoặc sẽ dính đúng bug đã gặp với `runner` (M1-4: process kẹt vĩnh viễn ở PLANNING vì retry gặp CONFLICT). Không có gì (test, lint, runtime check) BẮT BUỘC subscriber mới phải idempotent — chỉ là kỷ luật cá nhân | 9 | Trước khi thêm subscriber thật ở M5/M6: viết 1 test mẫu "gọi handler 2 lần liên tiếp với cùng envelope, kết quả phải giống hệt gọi 1 lần" — áp dụng cho MỌI subscriber mới, không chỉ review bằng mắt |
| RSK-23 | Phát hiện lúc thiết kế Render Agent (P-M3-5, doc backlog BL-007): `Registry.load_agent()` (P-M3-4) cache 1 INSTANCE Agent dùng chung cho mọi lần gọi cùng `agent_id@version`. Agent có state riêng theo lượt chạy (`self._ctx` gán ở `initialize()`, đọc lại ở `execute()`/`publish()`) — nếu 2 Process cùng dùng 1 agent chạy THẬT SỰ đồng thời (`Runner.worker_loop()`, M1-3a, `max_parallel` mặc định 3), instance dùng chung có thể bị 2 lượt gọi ghi đè state của nhau giữa các `await`. CHƯA xác nhận bug đã hiện thực hoá thật (chưa có test đối kháng 2 Process cùng agent chạy song song) | 12 | Viết test đối kháng trước khi tin tưởng thêm agent mới vào production thật: 2+ Process cùng `agent_id@version`, chạy qua `asyncio.gather`, assert artifact không lẫn dữ liệu giữa các Process. Nếu xác nhận: sửa `Registry.load_agent()` không cache instance (cache CLASS, khởi tạo instance mới mỗi lần gọi) |

---

## Quy tắc rà soát

Cuối mỗi milestone, trả lời 3 câu:
1. Rủi ro nào đã hiện thực hóa? Biện pháp có hiệu quả không?
2. Rủi ro nào đã biến mất (không cần theo dõi nữa)?
3. Rủi ro mới nào xuất hiện từ quyết định của milestone vừa rồi?

Đặc biệt luôn kiểm RSK-01 và RSK-03 — đây là hai rủi ro giết chết dự án một cách âm thầm nhất.
