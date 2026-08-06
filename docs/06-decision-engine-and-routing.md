# 06 — Decision Engine, Routing & các Engine tài nguyên

**Trạng thái:** v1.0

---

## 1. Decision Engine — "AI của AI"

Nhiệm vụ: từ JobSpec → chọn Workflow + tham số + chiến lược, **không hard-code**.

### 1.1 Quy trình 5 bước

```
JobSpec
  ↓
[1] Đặc trưng hóa (Feature Extraction)   — thuần tất định, không LLM
      input_type, mime, size, has_text_layer, image_count, language,
      duration_hint, privacy, budget, offline_only, hardware_state
  ↓
[2] Ứng viên (Candidate Generation)      — lọc từ Registry theo intent + input type
      video.from_pdf@2 · video.from_topic@1 · doc.summarize@1
  ↓
[3] Chấm điểm (Scoring)
      a) Luật cứng (hard rules) → loại thẳng ứng viên không hợp lệ
      b) Kinh nghiệm (Operational Knowledge) → tỉ lệ thành công quá khứ trên feature tương tự
      c) LLM tie-break → chỉ khi 2 ứng viên chênh < 5%
  ↓
[4] Lập tham số (Parameterization)       — nạp Preference từ Memory
  ↓
[5] Ghi DecisionRecord + phát decision.made
```

**Nguyên tắc quan trọng:** LLM là **trọng tài phụ**, không phải người quyết định chính. Lý do: LLM không tất định, không giải thích ổn định, và tốn tiền. Luật + thống kê quyết định 90% trường hợp.

### 1.2 Ví dụ minh họa
```
User: "Render Video" + file.pdf
[1] mime=pdf, has_text_layer=false, image_count=32, lang=vi
[2] {video.from_pdf@2, doc.summarize@1}
[3] hard rule: intent=video → loại doc.summarize
    has_text_layer=false → workflow phải chứa bước ocr → video.from_pdf@2 đạt
    lịch sử: 14 job tương tự, success 13/14, quality trung bình 87
[4] duration=75s (preference), tone=professional, voice=female
[5] DecisionRecord: chọn video.from_pdf@2 vì cần OCR; ước lượng 7 task, 9 phút, 0₫
```

### 1.3 Học từ quá khứ
Sau mỗi Process kết thúc, ghi vào bảng `decision_outcomes`:

```sql
CREATE TABLE decision_outcomes (
  decision_id TEXT, feature_hash TEXT, chosen TEXT,
  succeeded INTEGER, quality REAL, cost REAL, duration_ms INTEGER, user_edited INTEGER, at TEXT);
```

`feature_hash` = hash của vector đặc trưng đã rời rạc hóa. Lần sau gặp feature tương tự → tra bảng này trước. Đây chính là **Operational Knowledge** ở dạng thực dụng nhất.

## 2. Capability Router & Provider Ranking

### 2.1 Ràng buộc cứng (lọc trước khi chấm điểm)
Ứng viên bị loại nếu: không implement capability@version · `enabled=0` · breaker OPEN · health FAIL · vi phạm `privacy_class` của Job · vượt ngân sách còn lại · thiếu resource (vd không có GPU) · `offline_only=true` mà provider là cloud · vượt giới hạn context/kích thước input.

### 2.2 Công thức chấm điểm

```
score = w_q·Q̂ + w_c·(1 − Ĉ) + w_l·(1 − L̂) + w_p·P + w_r·R
```

| Ký hiệu | Ý nghĩa | Nguồn |
|---|---|---|
| `Q̂` | chất lượng chuẩn hóa 0–1 cho **task_class cụ thể** | `provider_stats.quality_ewma` |
| `Ĉ` | chi phí ước lượng chuẩn hóa | `estimate()` + `cost_model` |
| `L̂` | độ trễ chuẩn hóa | `latency_p50` |
| `P` | mức phù hợp quyền riêng tư (local=1.0, cloud=0.4) | manifest |
| `R` | độ tin cậy = success_rate gần đây | `provider_stats` |

Trọng số nằm trong `policies/routing.yaml` — **dữ liệu, không phải code**:

```yaml
version: 3
default: {w_q: 0.40, w_c: 0.35, w_l: 0.15, w_p: 0.05, w_r: 0.05}
profiles:
  economy:  {w_q: 0.25, w_c: 0.60, w_l: 0.10, w_p: 0.05, w_r: 0.00}
  quality:  {w_q: 0.70, w_c: 0.05, w_l: 0.10, w_p: 0.05, w_r: 0.10}
  private:  {w_q: 0.35, w_c: 0.20, w_l: 0.10, w_p: 0.35, w_r: 0.00}
rules:
  - if: "local_quality >= 80 and task_class != 'critical'"
    then: prefer_local            # nếu offline đủ tốt → không gọi cloud
  - if: "attempt >= 2"
    then: escalate_tier           # thử lần 2 thất bại → nâng lên provider mạnh hơn
  - if: "budget_used_pct > 80"
    then: force_profile economy
```

Minh họa bảng điểm quen thuộc (giá trị Q thô):
```
text.generate → DeepSeek 70 · Qwen 82 · Claude 95 · GPT 96
```
Nhưng quyết định cuối **không** chỉ theo Q: nếu Qwen ≥ ngưỡng 80 và w_c = 0.35 thì Qwen thắng vì chi phí bằng 0. GPT chỉ được gọi khi prompt thực sự khó (task_class = critical, hoặc đã fail 2 lần).

### 2.3 Fallback chain
```
Provider #1 (điểm cao nhất)
   ↓ lỗi retryable
Provider #2  ← ghi capability.fallback.triggered + lý do
   ↓ lỗi
Provider #3
   ↓ hết ứng viên
FAIL rõ ràng: PROVIDER_DOWN + hint khắc phục
```
Giữa các lần: exponential backoff + jitter. Mỗi lỗi cập nhật breaker và `provider_stats`.

### 2.4 Vòng phản hồi chất lượng
```
capability.call.completed → StatsUpdater
quality.review.passed/rejected → cập nhật quality_ewma theo task_class
user.correction.made → phạt nặng provider đã sinh ra bản bị sửa (trọng số ×3)
```
EWMA: `q_new = α·q_observed + (1−α)·q_old`, `α = 0.2`. Cần tối thiểu `n ≥ 5` mới dùng thống kê, dưới đó dùng `quality_hint` trong manifest.

## 3. Cost Engine

### 3.1 Mô hình chi phí
```yaml
# providers/*.yaml
cost: {unit: token, in: 0.0012, out: 0.0048, currency: JPY}   # cloud
cost: {unit: second, rate: 0, currency: JPY}                  # local (điện tính riêng)
```

Tham chiếu tương đối (dùng để minh họa nguyên tắc, cập nhật theo giá thực tế):
```
Offline 0¥ · Gemini 2¥ · GPT 5¥ · Claude 8¥   (trên một đơn vị công việc tương đương)
```

### 3.2 Ba mốc kiểm soát
1. **Trước Job:** ước lượng tổng → nếu > `budget.max` → hỏi người dùng hoặc chuyển profile `economy`.
2. **Trong Job:** kiểm tra ngân sách còn lại trước mỗi capability call. Vượt → `BUDGET_EXCEEDED`, thử fallback local.
3. **Sau Job:** ghi `cost_entries` thực tế; so với ước lượng để hiệu chỉnh `estimate()`.

### 3.3 Ngân sách nhiều tầng
```yaml
# policies/budget.yaml
budgets:
  per_job:   {max: 20,   currency: JPY, on_exceed: ask}
  per_day:   {max: 200,  currency: JPY, on_exceed: force_local}
  per_month: {max: 3000, currency: JPY, on_exceed: block_cloud}
warn_at_pct: 80
```

### 3.4 Cache là công cụ tiết kiệm số 1
Content-addressed cache (doc 03 §5.2): chạy lại Job giống hệt = 0₫. Mọi `capability.cache.hit` ghi lại `saved_cost` → báo cáo "tháng này cache đã tiết kiệm X₫".

## 4. Energy Engine (tài nguyên máy)

Theo dõi: GPU util/VRAM, CPU load, RAM, disk, nhiệt độ, pin/AC.

```yaml
# policies/energy.yaml
gpu:
  max_util_to_start: 60        # GPU đang > 60% → không cấp gpu token, xếp hàng chờ
  min_free_vram_mb: 4096
cpu:
  max_load_to_start: 0.75
battery:
  on_battery: {defer_heavy: true, allow: [text.generate]}
thermal:
  throttle_above_c: 85
```

Hành vi: Task cần `gpu:1` mà GPU bận → Process chuyển `WAITING`, phát `resource.wait.started`, **không chạy đè**. Khi rảnh → tự tiếp tục từ checkpoint.

## 5. Time Engine (cửa sổ thời gian)

```yaml
# policies/time.yaml
windows:
  - name: work_hours
    days: [mon,tue,wed,thu,fri]
    from: "08:00" to: "18:00"
    policy: {allow_heavy: false, max_parallel: 1, notify: silent}
  - name: night
    from: "21:00" to: "07:00"
    policy: {allow_heavy: true, max_parallel: 4}
default: {allow_heavy: true, max_parallel: 2}
```

Ví dụ: bạn đi làm 8h–18h → PAOS không render, xếp Job vào hàng đợi; 21h → tự động chạy. Job có `deadline` được miễn trừ nhưng phải ghi Decision Record giải thích vì sao vượt rào.

## 6. Sự phối hợp của 4 engine

Trước khi Scheduler cấp phát một Task, nó hỏi lần lượt và **bất kỳ ai từ chối cũng làm Task phải chờ**:

```
Scheduler
  ├─ Permission Guard : có được phép không?          → DENY = fail ngay
  ├─ Time Engine      : giờ này chạy được không?      → NO = chờ đến cửa sổ kế
  ├─ Energy Engine    : máy có tài nguyên không?      → NO = chờ token
  ├─ Cost Engine      : còn ngân sách không?          → NO = hạ cấp xuống local hoặc hỏi
  └─ Capability Router: ai phục vụ được?              → không ai = FAIL rõ ràng
Mọi câu trả lời "không" đều sinh Event + Decision Record.
```

Đây là lý do người dùng luôn trả lời được câu hỏi *"vì sao Job của tôi chưa chạy?"* — có đúng một nơi để nhìn.
