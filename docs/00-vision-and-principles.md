# 00 — Vision & Principles

**Trạng thái:** Chốt (v1.0) · **Loại:** Hiến pháp dự án · **Chỉ được sửa bằng ADR**

---

## 1. Vision

> Xây dựng một **Hệ điều hành AI cá nhân** có khả năng học hỏi, cộng tác, tự động hóa và mở rộng vô hạn, hoạt động chủ yếu trên máy cá nhân, với chi phí gần như bằng 0 sau khi hoàn thành nền tảng.

Đây là câu Vision **duy nhất**. Mọi quyết định kỹ thuật đều phải trả lời được:

> **"Điều này có giúp PAOS tồn tại được trong 10 năm nữa không?"**
> Nếu không → **Không làm.**

## 2. Mission (mục tiêu tối thượng)

Không phải xây dựng AI thông minh nhất. Mà xây dựng một hệ điều hành có khả năng **tích lũy, bảo tồn và phát huy tri thức của chính bạn** trong nhiều năm, bất kể các mô hình AI bên ngoài thay đổi như thế nào.

Hệ quả trực tiếp: **model là động cơ, có thể thay. Kiến trúc + quy trình + kinh nghiệm tích lũy mới là tài sản.**

## 3. Chuyển dịch tư duy nền tảng

Tư duy ChatBot (sai với mục tiêu này):

```
User → LLM → Kết quả
```

Tư duy Operating System (đúng):

```
User
 ↓
Operating System (Kernel)
 ↓
Planner
 ↓
Decision Engine
 ↓
Workflow Engine
 ↓
Agent System
 ↓
Capability Layer
 ↓
Provider Layer
 ↓
Result
```

**AI chỉ là một lớp rất nhỏ (Provider Layer) trong toàn bộ hệ thống.** Nếu AI chiếm hơn 20% độ phức tạp kiến trúc của bạn, bạn đang xây một chatbot có vỏ đẹp, không phải một hệ điều hành.

## 4. Mượn triết lý từ OS thật

| Khái niệm OS | Trong PAOS | Ý nghĩa |
|---|---|---|
| Kernel | PAOS Kernel | Điều phối, không biết gì về AI |
| Process | Process | Một công việc dài hạn có PID, trạng thái, tiến độ |
| Scheduler | Scheduler | Chạy song song, ưu tiên, chờ tài nguyên |
| Filesystem | Workspace | Mọi dữ liệu nằm trong Project, không có dữ liệu ẩn |
| Memory | Memory Tiers | 5 tầng từ tức thời đến tri thức cá nhân |
| Driver | Provider Adapter | Lớp dịch giữa Capability và thế giới bên ngoài |
| Syscall | Capability | Agent chỉ được "xin dịch vụ", không gọi thẳng phần cứng |
| Permission | Permission Model | Sandbox, xác nhận thao tác nguy hiểm |
| Application | Agent / Plugin | Cài thêm mà không sửa Kernel |
| Signal / IPC | Event Bus | Giao tiếp lỏng lẻo, không gọi trực tiếp |

## 5. 12 Nguyên tắc kiến trúc (P1–P12)

### P1 — Kernel mù AI (AI-blind Kernel)
Kernel không bao giờ biết GPT, Claude, Flux, Video là gì. Nó chỉ biết: Job, Process, Task, State, Memory, Event, Capability.
**Bài kiểm tra:** `grep -ri "openai\|anthropic\|gpt\|claude\|comfyui" kernel/` phải trả về **0 kết quả**. Đây là CI check bắt buộc.

### P2 — Local-first, Cloud-optional
Mặc định mọi thứ chạy được offline. Cloud là tùy chọn tăng chất lượng, không phải điều kiện sống.
**Bài kiểm tra:** rút mạng, PAOS vẫn khởi động, vẫn chạy được ít nhất một workflow đầy đủ.

### P3 — Capability trước Provider
Agent nói *"tôi cần sinh ảnh"*, không nói *"gọi Flux"*. Agent không bao giờ biết tên provider.
**Bài kiểm tra:** `grep -ri "provider_id\|model=" agents/` phải trả về 0 kết quả.

### P4 — Loose Coupling qua Event
Agent không gọi Agent. Agent phát Event, Agent khác lắng nghe. Thêm Agent mới không được sửa Agent cũ.
**Bài kiểm tra:** thêm Thumbnail Agent chỉ được tạo file mới, 0 dòng sửa ở Planning Agent.

### P5 — Mọi thứ đều tường minh (Explainable by default)
Mọi quyết định của hệ thống đều ghi lại: chọn gì, vì sao, các lựa chọn khác, điểm số, chi phí, thời gian. Không có "hộp đen".

### P6 — Tri thức là tài sản, code là chi phí
Ưu tiên thiết kế nào làm hệ thống *ghi nhớ được kinh nghiệm*. Một tính năng không tạo ra tri thức tích lũy có giá trị thấp hơn tính năng tạo ra nó.

### P7 — Dữ liệu thuộc về người dùng, ở định dạng mở
JSON / SQLite / Markdown / file media chuẩn. Không định dạng độc quyền, không mã hóa khóa cứng. Xuất toàn bộ workspace bất cứ lúc nào.

### P8 — Fail visible, never silent
Không nuốt lỗi. Mọi lỗi có mã, có ngữ cảnh, có gợi ý khắc phục, có ghi vào Event Log.

### P9 — Idempotent & Resumable
Tắt máy giữa chừng, mở lại phải chạy tiếp từ checkpoint gần nhất. Chạy lại một Task hai lần không được tạo ra kết quả sai hoặc tính tiền hai lần.

### P10 — Contract ổn định, phần thịt tự do
Chỉ 4 thứ được coi là hợp đồng dài hạn: **Kernel API, Capability Contract, Agent Contract, Event Schema**. Thay đổi chúng cần ADR. Mọi thứ khác được phép viết lại thoải mái.

### P11 — Boring technology
Chọn công nghệ chán nhưng sống lâu (SQLite, JSON, YAML, filesystem) thay vì công nghệ mới nhưng có thể chết trong 3 năm.

### P12 — An toàn mặc định
Thao tác không thể hoàn tác luôn cần xác nhận. Xóa = chuyển vào Trash. Plugin chạy trong sandbox với quyền khai báo tường minh.

## 6. Anti-goals (những gì PAOS cố tình KHÔNG làm)

- ❌ Không phải sản phẩm SaaS đa người dùng. v1 là **một người, một máy**.
- ❌ Không cạnh tranh với ChatGPT về chất lượng model.
- ❌ Không huấn luyện model nền (foundation model).
- ❌ Không xây UI đẹp trước khi Kernel đúng.
- ❌ Không tối ưu sớm cho quy mô không tồn tại (không Kubernetes, không microservice, không message broker ngoài).
- ❌ Không hỗ trợ real-time streaming/multi-modal live ở v1.

## 7. Chỉ số thành công (đo sau 12 tháng)

| Chỉ số | Mục tiêu |
|---|---|
| Chi phí biên trung bình mỗi Job | ≤ 2.000 ₫ (≈1¥), 80% Job = 0 ₫ |
| Tỷ lệ Job hoàn tất không cần can thiệp tay | ≥ 85% |
| Thời gian thêm một Capability mới | ≤ 1 ngày |
| Thời gian thay một Provider (vd Claude→Qwen) | ≤ 2 giờ, 0 dòng sửa trong Kernel/Agent |
| Số dòng sửa Kernel khi thêm Plugin mới | **0** |
| Số Decision Record có thể giải thích được | 100% |
| Kích thước Knowledge Graph cá nhân | Tăng đều, không giảm |

## 8. Điều khoản bảo tồn

Bất kỳ Pull Request nào vi phạm P1, P3, P4 hoặc P10 **bị từ chối mặc định**, kể cả khi nó chạy đúng và nhanh hơn. Lý do: cái giá của việc phá vỡ ranh giới không hiện ra trong tuần này, nó hiện ra vào năm thứ ba.
