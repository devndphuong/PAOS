# 11 — Non-Functional Requirements & SLO

**Trạng thái:** v1.0 · Mọi mục dưới đây phải **đo được**. NFR không đo được là NFR không tồn tại.

---

## 1. Hiệu năng

| ID | Yêu cầu | Ngưỡng | Cách đo |
|---|---|---|---|
| PERF-01 | Kernel overhead mỗi Task (không tính thời gian provider) | < 50ms p95 | span `task:*` trừ span con |
| PERF-02 | Độ trễ dispatch Event | < 20ms p95 | span nội bộ Event Bus |
| PERF-03 | Khởi động `paosd` | < 2s | đo lạnh, 10 lần |
| PERF-04 | Phản hồi API cục bộ (đọc) | < 100ms p95 | benchmark |
| PERF-05 | Resume sau crash | < 5s tới khi Task đầu tiên chạy lại | chaos test |
| PERF-06 | Truy vấn memory (retrieval hoàn chỉnh) | < 300ms p95 với 100k mục | benchmark |
| PERF-07 | `paosctl explain` với Process 200 task | < 1s | benchmark |
| PERF-08 | Số Process song song hỗ trợ | ≥ 8 | load test |

## 2. Độ tin cậy

| ID | Yêu cầu | Ngưỡng |
|---|---|---|
| REL-01 | **Không mất Event** kể cả khi kill -9 | 0 mất mát (ghi trước dispatch) |
| REL-02 | Không hỏng `state.db` khi mất điện đột ngột | 0 lỗi trong 100 lần test |
| REL-03 | Process dài (> 30 phút) resume thành công | ≥ 99% |
| REL-04 | Plugin crash không làm chết Kernel | 100% |
| REL-05 | Provider chết → fallback thành công | ≥ 95% (khi còn ứng viên) |
| REL-06 | Task idempotent: chạy lại không tạo chi phí trùng | 100% |
| REL-07 | Tự phục hồi sau restart máy | tự động, không cần thao tác tay |

## 3. Khả năng chạy độc lập (Local-first)

| ID | Yêu cầu |
|---|---|
| LOC-01 | Rút mạng → PAOS khởi động, chạy được ≥ 1 workflow hoàn chỉnh end-to-end |
| LOC-02 | ≥ 80% Job hoàn tất với chi phí 0₫ sau 6 tháng vận hành |
| LOC-03 | Không có tính năng nào **bắt buộc** phải có tài khoản cloud |
| LOC-04 | Cài đặt không cần Docker, không cần dịch vụ nền ngoài |
| LOC-05 | Chạy được trên máy không GPU ở chế độ degraded (chậm hơn, không lỗi) |

## 4. Khả năng bảo trì & tiến hóa (quan trọng nhất cho mục tiêu 10 năm)

| ID | Yêu cầu | Ngưỡng | Kiểm tra |
|---|---|---|---|
| MNT-01 | Kernel không phụ thuộc AI SDK | 0 import | CI grep, chạy mỗi PR |
| MNT-02 | Thêm Provider mới | ≤ 200 dòng, 0 dòng sửa Kernel/Agent | review |
| MNT-03 | Thêm Capability mới | ≤ 1 ngày công | thực nghiệm |
| MNT-04 | Thay Provider (Claude→Qwen) | ≤ 2 giờ, chỉ sửa YAML | thực nghiệm |
| MNT-05 | Thêm Plugin | 0 dòng sửa Kernel | CI: build Kernel không có `plugins/` |
| MNT-06 | Xóa toàn bộ `providers/` + `agents/` | Kernel vẫn build + test pass | CI job riêng |
| MNT-07 | Coverage Kernel | ≥ 85% | CI |
| MNT-08 | Mọi module Kernel < 500 dòng | cảnh báo khi vượt | lint |
| MNT-09 | Tài liệu đồng bộ với code | PR sửa contract phải sửa `docs/` | CI check |

## 5. Khả năng mở rộng (Scale — quy mô cá nhân)

| ID | Yêu cầu | Ngưỡng |
|---|---|---|
| SCL-01 | Số Event trong DB | ≥ 5.000.000 vẫn truy vấn < 300ms |
| SCL-02 | Số Project | ≥ 1.000 |
| SCL-03 | Số Artifact | ≥ 200.000 |
| SCL-04 | Số node KG | ≥ 100.000 |
| SCL-05 | Kích thước `state.db` sau 3 năm | < 5GB (nhờ nén event progress) |

## 6. Bảo mật (SLO đo được)

| ID | Yêu cầu |
|---|---|
| SEC-01 | 0 secret xuất hiện trong log/event/artifact — test tự động quét mẫu |
| SEC-02 | 100% hành động tier CONFIRM có bản ghi phê duyệt trong `audit_log` |
| SEC-03 | 100% hành động FORBIDDEN bị chặn — test đối kháng mỗi release |
| SEC-04 | 0 truy cập filesystem ngoài phạm vi khai báo của plugin — sandbox test |
| SEC-05 | 100% dữ liệu gửi ra cloud có bản ghi trong Trace |

## 7. Khả dụng & Trải nghiệm

| ID | Yêu cầu |
|---|---|
| UX-01 | Mọi lỗi hiển thị cho người dùng đều có `hint` hành động cụ thể |
| UX-02 | Mọi Process đang chạy > 30s đều có tiến độ cập nhật ≥ 1 lần/5s |
| UX-03 | Mọi con số trong UI đều truy được về nguồn (click → explain) |
| UX-04 | CLI có đủ 100% chức năng trước khi UI được xây |
| UX-05 | Người dùng huỷ Job bất kỳ lúc nào, dừng thật trong ≤ 5s |

## 8. Tính di động & Sở hữu dữ liệu

| ID | Yêu cầu |
|---|---|
| POR-01 | Xuất toàn bộ Workspace ra định dạng mở, đọc được không cần PAOS |
| POR-02 | Không có định dạng độc quyền hay mã hóa khóa cứng |
| POR-03 | KG xuất được JSON-LD; Memory xuất được JSON |
| POR-04 | Gỡ cài đặt = xóa 1 thư mục, không để lại rác trong hệ thống |
| POR-05 | Di chuyển workspace sang máy khác chỉ bằng copy thư mục |

## 9. Ma trận đánh đổi đã chấp nhận

| Ưu tiên cao | Hy sinh có ý thức |
|---|---|
| Tính tiến hóa 10 năm | Tốc độ ra tính năng ngắn hạn |
| Chạy cục bộ, chi phí 0 | Chất lượng đầu ra tuyệt đối |
| Giải thích được mọi thứ | Một chút overhead lưu trữ & hiệu năng |
| An toàn dữ liệu | Sự tiện lợi (nhiều lần xác nhận hơn) |
| Đơn giản vận hành (1 máy) | Khả năng mở rộng nhiều người dùng |
| Công nghệ chán, sống lâu | Công nghệ mới, hào nhoáng |

**Đọc lại bảng này mỗi khi bạn muốn "làm nhanh cho xong".**
