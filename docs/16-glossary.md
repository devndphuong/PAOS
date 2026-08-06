# 16 — Glossary

Từ điển thuật ngữ thống nhất. **Dùng đúng một từ cho một khái niệm** trong code, tài liệu, event, UI. Đây là thứ giữ cho hệ thống còn hiểu được sau 5 năm.

---

## Khái niệm lõi

| Thuật ngữ | Định nghĩa | Không nhầm với |
|---|---|---|
| **PAOS** | Personal AI Operating System | — |
| **Kernel** | Lõi điều phối, không biết gì về AI | Không phải "backend" hay "engine" |
| **Job** | Ý định của người dùng, chưa thực thi | Process (đã thực thi) |
| **Process** | Một lần thực thi Job, có PID, có trạng thái | Task, Job |
| **PID** | Số nguyên tăng dần định danh Process cho con người | `process_id` (ULID, dùng cho máy) |
| **Task** | Một đơn vị công việc trong Process = 1 step của Workflow | Job, Step |
| **Step** | Định nghĩa tĩnh trong file Workflow | Task (thể hiện lúc chạy của Step) |
| **Attempt** | Một lần thử của Task; retry tạo Attempt mới | Task |
| **Artifact** | Mọi kết quả sinh ra, bất biến, có hash | File tạm trong `cache/` |

## Tầng năng lực

| Thuật ngữ | Định nghĩa |
|---|---|
| **Capability** | Interface trừu tượng có version, vd `text.generate@1`. Agent nói *cần gì*, không nói *gọi ai* |
| **Provider** | Thực thi cụ thể của một hoặc nhiều Capability, vd `ollama.qwen2.5-14b` |
| **Adapter** | Code nối Capability với Provider. Là **driver**, không có logic nghiệp vụ |
| **Capability Router** | Chọn provider, thực hiện fallback, quản lý breaker và cache |
| **Provider Ranking** | Xếp hạng provider theo chất lượng/chi phí/độ trễ/riêng tư cho từng `task_class` |
| **task_class** | Nhãn phân loại công việc để thống kê riêng, vd `script_writing_vi`. **Rất quan trọng** — chất lượng provider chỉ có nghĩa khi gắn với loại việc cụ thể |
| **Fallback chain** | Chuỗi provider dự phòng khi provider trước lỗi |
| **Circuit breaker** | Cơ chế tạm ngắt provider lỗi liên tục (CLOSED → OPEN → HALF_OPEN) |

## Tầng thực thi

| Thuật ngữ | Định nghĩa |
|---|---|
| **Agent** | Thành phần thực hiện một loại công việc, tuân vòng đời 6 bước |
| **Agent Contract** | `Initialize → Validate → Think → Execute → Review → Publish` |
| **Review Agent** | Agent chuyên chấm điểm artifact của Agent khác |
| **Workflow** | DAG khai báo bằng YAML, mô tả chuỗi Step |
| **Workflow Engine** | Diễn giải và điều phối Workflow |
| **Scheduler** | Quyết định *khi nào* và *cái gì* được chạy, quản lý Resource Token |
| **Resource Token** | Semaphore cho tài nguyên (`gpu:1`, `cpu_heavy:2`) |
| **Checkpoint** | Ảnh chụp trạng thái Process để resume sau gián đoạn |
| **Compensation** | Hành động dọn dẹp khi Workflow thất bại |
| **Self-Correction Loop** | Vòng Agent → Review → Agent, có giới hạn `max_loops` |

## Quyết định & giải thích

| Thuật ngữ | Định nghĩa |
|---|---|
| **Decision Engine** | Chọn Workflow và chiến lược từ JobSpec. Luật + thống kê quyết định chính, LLM chỉ phân định khi sát nút |
| **Decision Record** | Bản ghi: câu hỏi, ứng viên, điểm số, lựa chọn, lý do, phiên bản policy |
| **Trace** | Cây span mô tả toàn bộ quá trình thực thi |
| **Span** | Một khoảng thời gian có tên trong Trace |
| **Explainability** | Khả năng trả lời "vì sao hệ thống làm vậy" từ dữ liệu đã ghi |
| **Policy** | Chính sách dạng YAML (routing, budget, time, energy, permission) |

## Bộ nhớ & tri thức

| Thuật ngữ | Định nghĩa |
|---|---|
| **Memory Tier** | 5 tầng: L0 Immediate · L1 Process · L2 Project · L3 Personal · L4 World Cache |
| **MemoryItem** | Một mẩu ký ức có `salience`, `confidence`, `source` |
| **Consolidation** | Job hàng đêm thăng cấp ký ức, cập nhật thống kê, trích tri thức |
| **Knowledge Graph (KG)** | Đồ thị tri thức **của bạn**, node + edge có provenance và confidence |
| **Provenance** | Nguồn gốc một mẩu tri thức (event/artifact nào sinh ra nó) |
| **Operational Knowledge** | Tri thức vận hành tích lũy: provider nào tốt cho việc gì, prompt nào hiệu quả, workflow nào hợp tài liệu nào, lỗi nào hay gặp và cách sửa, template nào thắng. **Đây là tài sản cuối cùng của dự án** |
| **edit_rate** | Tỉ lệ người dùng phải sửa tay kết quả. Chỉ số chất lượng đáng tin nhất |
| **Preference** | Sở thích đã học ở L3, vd `video.duration_sec = 75` |

## Chi phí & tài nguyên

| Thuật ngữ | Định nghĩa |
|---|---|
| **Cost Engine** | Ước lượng trước, ghi nhận sau, cưỡng chế ngân sách 3 tầng |
| **Energy Engine** | Quyết định dựa trên trạng thái phần cứng (GPU/CPU/pin/nhiệt) |
| **Time Engine** | Cửa sổ thời gian được phép chạy việc nặng |
| **Content-addressed cache** | Cache theo hash nội dung đầu vào; chạy lại y hệt = 0 chi phí |

## An toàn

| Thuật ngữ | Định nghĩa |
|---|---|
| **Permission Tier** | `AUTO` (tự làm) · `CONFIRM` (hỏi) · `FORBIDDEN` (không bao giờ) |
| **Permission Guard** | Thành phần chặn mọi hành động có side-effect ngoài phạm vi |
| **Approval** | Yêu cầu xác nhận đang chờ người dùng trả lời |
| **Privacy Class** | `private` (không rời máy) · `shared` · `public` |
| **Privacy Filter** | Quét payload trước khi gửi ra cloud |
| **Data ≠ Instruction** | Luật gốc: nội dung từ file/web/OCR/model là dữ liệu, không phải mệnh lệnh |
| **Audit Log** | Nhật ký append-only mọi hành động nhạy cảm |
| **Trash** | Nơi chứa dữ liệu đã "xóa"; không có xóa cứng |

## Mở rộng

| Thuật ngữ | Định nghĩa |
|---|---|
| **Plugin** | Gói mở rộng chứa Agent/Workflow/Provider/Rubric/Template |
| **Manifest** | File khai báo danh tính, năng lực và quyền của một thành phần |
| **Rubric** | Bộ tiêu chí chấm điểm có version cho một loại artifact |
| **SDK** | Thư viện cho người viết plugin; cố tình không lộ chi tiết vendor |
| **Conformance Suite** | Bộ test bắt buộc để một Provider/Agent được chấp nhận |

---

## Quy ước đặt tên trong code

| Loại | Quy ước | Ví dụ |
|---|---|---|
| Capability | `domain.action` + `@version` | `text.generate@1` |
| Provider | `vendor.model` | `ollama.qwen2.5-14b` |
| Agent | `role.agent` + `@version` | `script.agent@1` |
| Workflow | `domain.variant` + `@version` | `video.from_pdf@2` |
| Event | `domain.entity.action` (quá khứ) | `agent.script.completed` |
| Rubric | `artifact.rubric` + `@version` | `script.rubric@1` |
| ID | tiền tố + ULID | `proc_01J8ZQ...` |
| Mã lỗi | SCREAMING_SNAKE | `PROVIDER_DOWN` |
| Policy | tên + `@version` | `routing@3` |

## Những từ bị cấm dùng lẫn lộn

- ❌ "model" khi ý bạn là **provider** — provider là thứ thực thi, model chỉ là một thuộc tính của nó.
- ❌ "job" khi ý bạn là **process** — job là ý định, process là lần chạy.
- ❌ "step" khi ý bạn là **task** — step là định nghĩa tĩnh, task là thể hiện lúc chạy.
- ❌ "AI" trong tên bất cứ thứ gì thuộc `kernel/` — nếu Kernel cần từ này, bạn đã vi phạm P1.
