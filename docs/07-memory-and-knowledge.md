# 07 — Memory, Knowledge Graph & Operational Knowledge

**Trạng thái:** v1.0 · **Đây là tài liệu về TÀI SẢN của dự án.** Nếu mất hết code nhưng giữ được dữ liệu ở đây, PAOS vẫn có giá trị. Ngược lại thì không.

---

## 1. Năm tầng bộ nhớ

```
L0 Immediate      — trong một Task, sống vài giây          | RAM
L1 Process        — trong một Process, sống vài phút–giờ    | RAM + checkpoint
L2 Project        — trong một Project, sống nhiều tháng     | SQLite + file
L3 Personal       — về BẠN, sống nhiều năm                  | SQLite (quan trọng nhất)
L4 World Cache    — tri thức ngoài đã tra cứu, có TTL       | SQLite + cache
```

| Tầng | Ví dụ nội dung | Ghi bởi | Xóa khi |
|---|---|---|---|
| L0 | context cửa sổ prompt hiện tại | Agent | Task kết thúc |
| L1 | plan, script nháp, kết quả bước trước | Workflow Engine | Process kết thúc (giữ ở artifact) |
| L2 | phong cách của dự án này, nhân vật, thuật ngữ riêng | MemoryWriter | Project bị xóa |
| L3 | "thích video 60–90s", "tone chuyên nghiệp", "giọng nữ", "không dùng emoji" | Consolidator | Người dùng gỡ tay |
| L4 | tài liệu MongoDB đã đọc, kết quả tìm kiếm | KnowledgeExtractor | TTL hết hạn |

**Ví dụ cụ thể:** bạn luôn chọn video 60–90s, tone Professional, giọng Female → sau 3 lần, L3 ghi lại với `confidence` tăng dần. Từ lần thứ 4, **PAOS không hỏi lại nữa**, chỉ hiển thị "đang dùng sở thích của bạn: 75s / professional / female — sửa?"

## 2. Vòng đời một mẩu ký ức

```
Quan sát (Event)
   ↓
Ứng viên (candidate, confidence thấp)
   ↓ lặp lại ≥ N lần hoặc người dùng xác nhận
Củng cố (consolidation job chạy mỗi đêm)
   ↓
Ký ức ổn định (L3, confidence cao)
   ↓ mâu thuẫn với quan sát mới
Xét lại → cập nhật / hạ tin cậy / vô hiệu hóa (không xóa, đánh dấu invalidated)
   ↓ lâu không dùng
Phai mờ (salience giảm) → chỉ còn trong lịch sử
```

### 2.1 Quy tắc học sở thích
```
confidence_mới = clamp(confidence + δ)
δ = +0.25 nếu người dùng xác nhận rõ ràng
δ = +0.10 nếu người dùng chấp nhận mặc định (im lặng)
δ = −0.40 nếu người dùng sửa tay (user.correction.made)
Áp dụng tự động khi confidence ≥ 0.75; chỉ gợi ý khi 0.4–0.75; bỏ qua khi < 0.4
```

### 2.2 Chống nhiễm bẩn (memory poisoning)
- Không bao giờ ghi L3 trực tiếp từ output của LLM. Chỉ ghi từ **hành vi quan sát được** của người dùng hoặc từ xác nhận tường minh.
- Mọi MemoryItem có `source_json` truy vết đến event/artifact gốc.
- `paosctl memory review` — duyệt các ứng viên đang chờ vào L3 (mặc định hàng tuần).

## 3. Truy hồi (Retrieval)

Chiến lược lai, chạy theo thứ tự:

```
1. Exact key lookup      (preference, template) — nhanh, tất định, ưu tiên cao nhất
2. Knowledge Graph walk  (2 hop từ entity trong yêu cầu)
3. Vector search         (top-k = 8, ngưỡng cosine ≥ 0.62)
4. Recency boost         (dùng gần đây × 1.2)
5. Rerank + cắt theo ngân sách token (mặc định ≤ 2000 token context memory)
```

Kết quả truy hồi luôn kèm nguồn để đưa vào Trace: agent nào dùng ký ức nào.

## 4. Knowledge Graph cá nhân

**Không phải Internet. Của bạn.**

### 4.1 Loại node
`Concept · Technology · Tool · Project · Artifact · Person · Preference · Workflow · Provider · Error · Template · Source`

### 4.2 Loại quan hệ
`is_a · part_of · used_in · depends_on · alternative_to · replaced_by · prefers · avoids · causes · fixes · produced_by · learned_from · similar_to`

### 4.3 Ví dụ tăng trưởng
```
Lần 1: bạn học MongoDB
   (MongoDB) -[is_a]-> (Database)
   (MongoDB) -[learned_from]-> (Source: mongodb.pdf)

Lần 2: bạn làm video về PostgreSQL
   (PostgreSQL) -[is_a]-> (Database)
   (PostgreSQL) -[alternative_to]-> (MongoDB)

Lần 3: bạn hỏi về Redis
   (Redis) -[is_a]-> (Cache)
   → PAOS đã biết bối cảnh "Database" của bạn, gợi ý so sánh với MongoDB
```
Sau một năm, đồ thị này mô tả **cách bạn hiểu thế giới**, không phải cách Wikipedia hiểu.

### 4.4 Chất lượng đồ thị
- Mỗi edge có `confidence` + `provenance` (event nào, artifact nào, do ai khẳng định).
- Phát hiện mâu thuẫn → `knowledge.conflict.detected` → giữ cả hai, đánh dấu, hỏi người dùng khi thuận tiện.
- Không xóa: dùng `invalidated_at` để giữ lịch sử nhận thức.
- Xuất `knowledge/graph.jsonld` mỗi tuần → đọc được bằng công cụ khác, chống lock-in.

## 5. Operational Knowledge — mục tiêu tối thượng

Đây là thứ mà sau nhiều năm sẽ quý hơn mọi Agent và mọi model. PAOS phải **chủ động** sinh ra nó, không để nó tự nhiên xuất hiện.

### 5.1 Năm loại tri thức vận hành cần tích lũy

| Loại | Nguồn dữ liệu | Bảng lưu | Dùng ở đâu |
|---|---|---|---|
| **1. Khi nào dùng offline, khi nào dùng cloud** | `provider_stats` + `quality.review.*` | `provider_stats` | Provider Ranking |
| **2. Prompt nào cho kết quả tốt nhất** | quality score theo `prompt_version` | `prompt_stats` | Agent chọn prompt |
| **3. Quy trình nào hợp với loại tài liệu nào** | `decision_outcomes` theo `feature_hash` | `decision_outcomes` | Decision Engine |
| **4. Lỗi nào hay xảy ra và cách khắc phục** | `kernel.task.failed` + hành động sau đó | `error_playbook` | Auto-recovery + hint lỗi |
| **5. Template nào cho kết quả tốt nhất** | quality + `user.correction.made` | `template_stats` | Agent chọn template |

```sql
CREATE TABLE prompt_stats (
  agent_id TEXT, prompt_version TEXT, task_class TEXT,
  n INTEGER, quality_ewma REAL, edit_rate REAL, updated_at TEXT,
  PRIMARY KEY(agent_id, prompt_version, task_class));

CREATE TABLE error_playbook (
  error_code TEXT, context_hash TEXT, occurrences INTEGER,
  resolution TEXT, resolution_source TEXT,   -- 'auto' | 'user'
  success_rate REAL, updated_at TEXT,
  PRIMARY KEY(error_code, context_hash));

CREATE TABLE template_stats (
  template_id TEXT PRIMARY KEY, n INTEGER, quality_ewma REAL,
  user_kept_rate REAL, updated_at TEXT);
```

### 5.2 Vòng lặp học tự động (chạy mỗi đêm)
```
Consolidation Job (03:00)
 ├─ tổng hợp Event 24h qua
 ├─ cập nhật EWMA cho provider/prompt/template
 ├─ thăng cấp ứng viên memory đủ điều kiện lên L3
 ├─ trích node/edge mới cho KG, khử trùng lặp entity
 ├─ phát hiện mẫu lỗi lặp lại → ghi error_playbook
 ├─ sinh báo cáo tuần: "PAOS đã học được gì tuần này"
 └─ xuất knowledge/operational/*.md (dạng người đọc được)
```

### 5.3 Đầu ra cho con người
`workspace/knowledge/operational/` chứa Markdown được sinh tự động, ví dụ:

```markdown
# Playbook: Viết script video tiếng Việt
- Provider tốt nhất: qwen2.5-14b (Q 86, n=41) — vượt gpt-4 về độ tự nhiên trong 68% trường hợp
- Prompt tốt nhất: script/v5.md (edit_rate 12%, so với v3 là 41%)
- Lỗi thường gặp: script quá dài → thêm ràng buộc "≤ 180 từ" ở system prompt
- Template thắng: hook_question + 3_point_body + cta_soft
```

Đây là **tài sản có thể đọc được bằng mắt người**, tồn tại độc lập với code. Nếu 5 năm sau bạn viết lại PAOS từ đầu bằng ngôn ngữ khác, bạn vẫn giữ được toàn bộ giá trị này.

## 6. Quyền riêng tư & kiểm soát

**Đã triển khai (P-M5-4).**

- `paosctl memory list|show|forget <id>` — quyền xóa tuyệt đối thuộc về người dùng. `forget` là **xóa cứng thật** (không qua `trash/`, không khôi phục được) — ngoại lệ có chủ đích với chính sách xóa mềm chung (ADR-0012), xem ADR-0029 để biết lý do đầy đủ. Yêu cầu xác nhận tay ở CLI (`--yes` để bỏ qua trong script).
- Memory L3 **không bao giờ** được gửi tới provider `class: cloud`: `apps/paosd/router.py::Router.call(..., contains_private_l3=True)` chặn VÔ ĐIỀU KIỆN mọi candidate `provider_class == "cloud"`, bất kể provider đó tự khai `privacy:` gì trong `provider.yaml` (chống provider khai gian/cấu hình sai — xem test đối kháng `tests/apps/paosd/test_privacy_filter.py`). Nhánh "trừ khi Job có `privacy: shared` và người dùng đã đồng ý" CHƯA triển khai — Job.privacy_class thật và luồng đồng ý (M7/M8) chưa tồn tại; quy tắc rút gọn AN TOÀN hôm nay là chặn tuyệt đối, không có ngoại lệ.
- Trước mỗi capability call ra cloud có `contains_private_l3=True`, Privacy Filter ghi Decision Record (Trace) VÀ phát event riêng `privacy.cloud_send.blocked` — cả hai đều CỐ Ý không mang nội dung payload thật, chỉ mang provider_id/capability/decision_id.
- `contains_private_l3` là cờ do CALLER tự khai (Agent qua `AgentContext.call(..., contains_private_l3=True)`), không phải Router tự quét nội dung — đủ cho quy mô hôm nay vì **chưa Agent nào thật sự đọc lại Memory L3 để dùng** (docs/backlog.md BL-015/BL-019); cơ chế chặn đã CHẠY THẬT và kiểm bằng test đối kháng, sẵn sàng cho caller thật đầu tiên.
- Xuất/nhập toàn bộ memory dạng JSON để bạn tự soi và tự sửa: `paosctl memory export` / `paosctl memory import <file>` (không xuất vector embedding — BLOB nhị phân, ngoài phạm vi "tự soi/tự sửa nội dung").
- Knowledge Graph đi theo hướng NGƯỢC LẠI có chủ đích: không có `knowledge forget` — §4.4 "không xóa, dùng `invalidated_at`" áp dụng nguyên vẹn, xem ADR-0029.
