# 01 — Product Requirements Document (PRD)

**Trạng thái:** v1.0 · **Phạm vi:** PAOS v1 (12 tháng đầu)

---

## 1. Người dùng

**Primary persona — "Người vận hành đơn độc"**
Một người làm nhiều loại việc trí óc: sản xuất nội dung, nghiên cứu, xử lý tài liệu, học kỹ thuật. Có máy cá nhân đủ mạnh (hoặc sẽ có GPU). Muốn tự động hóa nhưng không muốn phụ thuộc và trả tiền hàng tháng cho cloud. Chấp nhận setup phức tạp ban đầu để đổi lấy quyền kiểm soát dài hạn.

**Secondary persona — "Người viết Plugin"**
Có thể là chính bạn của 2 năm sau, hoặc người khác. Cần mở rộng PAOS mà không đọc hết Kernel.

**Non-persona (không phục vụ ở v1):** đội nhóm nhiều người, doanh nghiệp, người dùng phổ thông không kỹ thuật.

## 2. Vấn đề cốt lõi

| Vấn đề | Hiện trạng | PAOS giải quyết bằng |
|---|---|---|
| Kinh nghiệm làm việc với AI bị mất sau mỗi phiên chat | Không có bộ nhớ dài hạn thực sự | Memory Tiers + Knowledge Graph + Operational Knowledge |
| Đổi model = viết lại toàn bộ | Code gắn chặt vào SDK vendor | Capability Layer + Provider Registry |
| Việc dài chạy được nửa chừng thì gián đoạn | Không có process/checkpoint | Process Manager + Checkpoint + Resume |
| Không biết vì sao hệ thống cho ra kết quả đó | Hộp đen | Decision Record + Trace + `paosctl explain` |
| Chi phí API không kiểm soát | Không có ngân sách/ước lượng | Cost Engine + Budget Policy |
| Chất lượng đầu ra không ổn định | Không có vòng kiểm định | Review Agent + Quality Score + Retry |
| Thêm tính năng = sửa lõi | Kiến trúc gắn kết chặt | Event Bus + Plugin System |

## 3. Use case bắt buộc của v1 (UC1–UC8)

### UC1 — Sản xuất video ngắn từ một chủ đề
**Input:** một câu chủ đề hoặc một file PDF.
**Luồng:** Decision Engine chọn workflow → Planning → Script → (Image ∥ Voice ∥ Subtitle) → Render → Review → Publish vào Project.
**Output:** file `.mp4` + toàn bộ artifact trung gian + Trace giải thích.
**Tiêu chí đạt:** chạy được 100% offline với chất lượng chấp nhận được; Scheduler chạy song song đúng; resume được sau khi tắt máy.

### UC2 — Dịch và tóm tắt tài liệu PDF nhiều hình
**Luồng:** phát hiện PDF scan → OCR → chunk → dịch → tóm tắt → xuất Markdown/DOCX.
**Tiêu chí đạt:** Decision Engine tự phát hiện cần OCR mà không hard-code.

### UC3 — Nghiên cứu một chủ đề kỹ thuật
**Luồng:** web search (nếu online) hoặc tài liệu local → tổng hợp → ghi vào Knowledge Graph.
**Tiêu chí đạt:** lần hỏi sau về chủ đề liên quan, PAOS dùng lại tri thức đã có, chỉ ra được nguồn gốc.

### UC4 — Chạy nhiều Process song song
Người dùng khởi động 3 Job cùng lúc, xem được PID/status/progress, huỷ được một Job mà không ảnh hưởng hai Job kia.

### UC5 — Thay Provider không sửa code
Tắt provider `gpt`, hệ thống tự fallback theo chuỗi, ghi lại Decision Record giải thích lý do fallback.

### UC6 — Cài Plugin mới
Cài plugin "Excel" → xuất hiện Capability + Agent + Workflow mới. **0 dòng sửa Kernel.**

### UC7 — Giải thích một Job đã chạy
`paosctl explain proc_01H...` → cây quyết định đầy đủ: workflow nào, agent nào, provider nào, prompt nào, mất bao lâu, tốn bao nhiêu, điểm chất lượng bao nhiêu, vì sao chọn.

### UC8 — Chạy theo lịch/điều kiện tài nguyên
Job nặng được xếp lịch chạy lúc 21h khi máy rảnh; PAOS chờ GPU trống thay vì chạy đè.

## 4. Yêu cầu chức năng (FR)

| ID | Yêu cầu | Ưu tiên |
|---|---|---|
| FR-01 | Tạo, chạy, tạm dừng, tiếp tục, huỷ Process | P0 |
| FR-02 | Xem trạng thái/tiến độ mọi Process đang chạy | P0 |
| FR-03 | Checkpoint sau mỗi Task; resume sau khi khởi động lại | P0 |
| FR-04 | Event Log bền vững, có thể replay | P0 |
| FR-05 | Đăng ký Capability và nhiều Provider cho mỗi Capability | P0 |
| FR-06 | Fallback chain tự động khi Provider lỗi | P0 |
| FR-07 | Provider Ranking dựa trên chất lượng/chi phí/độ trễ/quyền riêng tư | P1 |
| FR-08 | Workflow khai báo bằng YAML, có DAG, điều kiện, song song | P0 |
| FR-09 | Agent tuân theo vòng đời chuẩn 6 bước | P0 |
| FR-10 | Review Agent chấm điểm và yêu cầu làm lại khi dưới ngưỡng | P1 |
| FR-11 | Decision Engine tự chọn workflow từ input | P1 |
| FR-12 | Memory 5 tầng, tự ghi nhớ sở thích người dùng | P1 |
| FR-13 | Knowledge Graph cá nhân có provenance | P2 |
| FR-14 | Cost Engine: ước lượng trước, ghi nhận sau, ngân sách cứng | P1 |
| FR-15 | Energy Engine: chờ GPU/CPU rảnh | P2 |
| FR-16 | Time Engine: cửa sổ thời gian được phép chạy | P2 |
| FR-17 | Permission: thao tác nguy hiểm cần xác nhận | P0 |
| FR-18 | Trace + Decision Record cho mọi Job | P0 |
| FR-19 | Plugin cài/gỡ nóng, không sửa Kernel | P2 |
| FR-20 | Xuất/nhập toàn bộ Workspace | P1 |
| FR-21 | CLI `paosctl` đầy đủ chức năng trước khi có UI | P0 |
| FR-22 | Local Web UI: dashboard Process + Explain + Project browser | P2 |

## 5. Phạm vi

**Trong phạm vi v1:** Kernel, Scheduler, Event Bus, Capability/Provider, Agent, Workflow YAML, Memory, Decision Engine cơ bản (rule + LLM), Review/Quality, Cost/Energy/Time Engine, Permission, Trace, CLI, UI tối thiểu, Plugin SDK, 1 plugin mẫu (Video).

**Ngoài phạm vi v1:** đồng bộ nhiều máy, chia sẻ plugin công khai/thanh toán, multi-user, mobile app, fine-tuning model, real-time voice, tự sửa code của chính nó.

## 6. Ràng buộc

- Một người phát triển → phải ưu tiên nghiệt ngã, mọi thứ phải test tự động được.
- Máy cá nhân, có thể không có GPU trong 6 tháng đầu → mọi thành phần phải chạy được ở chế độ "degraded".
- Ngân sách API thấp → mặc định offline, cloud là ngoại lệ có ngân sách.
- Không có đội vận hành → hệ thống phải tự phục hồi, tự log, tự dọn rác.

## 7. Giả định & phụ thuộc

- Có sẵn runtime model local (Ollama/llama.cpp) và ComfyUI cho ảnh.
- Có `ffmpeg` cho video/audio.
- Người dùng chấp nhận CLI ở giai đoạn đầu.
- Các API cloud (nếu dùng) có giao diện tương thích chuẩn HTTP/JSON.
