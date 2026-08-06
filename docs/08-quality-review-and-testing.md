# 08 — Quality, Review Agent & Testing Strategy

**Trạng thái:** v1.0

---

## PHẦN A — CHẤT LƯỢNG ĐẦU RA

## 1. Nguyên tắc

> Script xong **không Render luôn**. Review Agent đọc trước.

Mỗi Output đều có điểm. Ví dụ điển hình: Script 91 · Image 84 · Voice 97 · Video 89. Dưới **80** → làm lại. Nhưng "làm lại" phải có giới hạn, có ngân sách, và có đường thoát ra.

## 2. Rubric — chấm điểm có cấu trúc

Rubric là file YAML có version, gắn với loại artifact:

```yaml
# rubrics/script.rubric.v1.yaml
id: script.rubric
version: 1
applies_to: script
threshold: 80
criteria:
  - id: logic
    weight: 0.25
    kind: llm            # llm | deterministic | hybrid
    question: "Có lỗi logic, mâu thuẫn, hay thông tin sai so với nguồn không?"
    scale: {0: "sai nghiêm trọng", 50: "có điểm mơ hồ", 100: "chặt chẽ"}
  - id: redundancy
    weight: 0.15
    kind: hybrid
    check: "n_gram_overlap(script) < 0.18"
    question: "Có ý nào bị lặp lại không?"
  - id: length
    weight: 0.15
    kind: deterministic
    check: "150 <= word_count <= 200"
  - id: hook
    weight: 0.20
    kind: llm
    question: "3 giây đầu có giữ chân người xem không?"
  - id: cta
    weight: 0.10
    kind: deterministic
    check: "has_section('cta')"
  - id: tone_match
    weight: 0.15
    kind: llm
    question: "Có đúng tone 'professional' theo sở thích người dùng không?"
fail_fast: [length, cta]    # rớt mấy tiêu chí này thì không cần gọi LLM, trả về ngay
```

**Nguyên tắc vàng:** tiêu chí nào kiểm được bằng code thì **không dùng LLM**. Kiểm tất định trước, rẻ và ổn định; LLM chỉ dùng cho phần thực sự cần phán đoán.

## 3. Review Agent

```
Artifact + Rubric + Context (nguồn, preference, feedback lần trước)
   ↓
[1] Deterministic checks          — độ dài, cấu trúc, schema, trùng lặp, cấm kỵ
   ↓ rớt fail_fast → REJECT ngay (0 chi phí LLM)
[2] LLM-as-judge                  — chấm từng criterion, bắt buộc trả JSON có schema
   ↓
[3] Tổng hợp điểm có trọng số → score 0–100
   ↓
[4] score ≥ threshold → PASS, ghi quality.review.passed
    score < threshold → REJECT + feedback CÓ CẤU TRÚC
```

Feedback bắt buộc có cấu trúc, không phải văn xuôi mơ hồ:

```json
{"score": 68, "verdict": "reject",
 "failed": [
   {"criterion": "hook", "score": 40,
    "issue": "Mở đầu bằng định nghĩa khô khan",
    "suggestion": "Bắt đầu bằng câu hỏi về vấn đề người xem gặp phải",
    "location": "line 1-2"}],
 "keep": ["cấu trúc 3 phần đang tốt, giữ nguyên"]}
```

`keep` quan trọng ngang `failed`: nó ngăn Agent viết lại từ đầu và làm hỏng phần đang tốt.

## 4. Self-Correction Loop — và giới hạn của nó

```
Script Agent → Review Agent
                  │ reject (kèm feedback)
                  ▼
             Script Agent (lần 2, có feedback + phần cần giữ)
                  │ reject
                  ▼
             Script Agent (lần 3, ĐỔI CHIẾN LƯỢC: prompt khác / provider mạnh hơn)
                  │ reject
                  ▼
             ESCALATE → hỏi người dùng, kèm bản tốt nhất + lý do
```

Quy tắc chống lặp vô ích:
1. `max_loops` mặc định **2** (tối đa 3 lần thử).
2. Điểm phải **cải thiện ≥ 5** giữa hai vòng, nếu không → dừng, escalate. Lặp không tiến bộ là lãng phí.
3. Ngân sách retry riêng: tối đa 30% ngân sách Job.
4. Vòng 3 **bắt buộc đổi chiến lược** (prompt version khác hoặc tier provider cao hơn), không lặp lại y hệt.
5. Escalate luôn kèm bản tốt nhất đã có — không bao giờ trả về tay trắng.

## 5. Chống "LLM tự chấm điểm mình"

Rủi ro rõ ràng: model vừa viết vừa chấm sẽ tự khen.

- Review **phải** dùng provider khác (hoặc ít nhất prompt/model instance khác) so với provider đã tạo artifact. Router cưỡng chế điều này qua ràng buộc `exclude_provider`.
- Định kỳ hiệu chuẩn: 20 artifact ngẫu nhiên/tháng được bạn chấm tay → so với điểm máy → tính độ lệch. Lệch > 15 điểm → chỉnh rubric hoặc đổi judge, ghi vào `knowledge/operational/`.
- Điểm số **không bao giờ** là mục tiêu duy nhất. Chỉ số quan trọng hơn là **`edit_rate`** — tỉ lệ bạn phải sửa tay. Đây là sự thật khách quan, không gian lận được.

---

## PHẦN B — CHIẾN LƯỢC KIỂM THỬ

## 6. Kim tự tháp test

```
        ╱ Eval (chất lượng AI) ╲        — chậm, không tất định, chạy hàng đêm/CI-nightly
      ╱  E2E / Golden Workflow  ╲       — chạy trước mỗi release
    ╱    Contract / Conformance   ╲     — chạy mỗi PR (bắt buộc)
  ╱        Integration              ╲   — chạy mỗi PR
╱            Unit                     ╲ — chạy liên tục
```

## 7. Các loại test bắt buộc

### 7.1 Unit
Kernel, scheduler, state machine, biểu thức workflow, chuẩn hóa cache key. Mục tiêu coverage Kernel ≥ **85%**.

### 7.2 Contract / Conformance (quan trọng nhất)
- **Provider conformance:** mọi provider phải pass bộ test của capability nó khai báo (doc 04 §2.3).
- **Agent conformance:** đủ 6 bước vòng đời, idempotent, resume được, không gọi capability ngoài khai báo.
- **Event schema:** mọi event phát ra phải validate với schema đã đăng ký.

### 7.3 Golden Workflow (test tất định với AI)
```
tests/golden/video_from_pdf/
├── input/sample.pdf
├── fixtures/            # phản hồi provider đã ghi lại (record & replay)
│   ├── doc.ocr.json
│   ├── text.generate.plan.json
│   └── image.generate.json
└── expected/
    ├── workflow_trace.json      # thứ tự step, nhánh song song, số task
    └── artifacts.manifest.json  # loại + số lượng artifact, KHÔNG so sánh nội dung byte
```
Chạy ở `PAOS_MODE=deterministic`: seed cố định, toàn bộ provider thay bằng fixture. **So sánh cấu trúc và quyết định, không so sánh nội dung do AI sinh ra** — nếu không test sẽ vỡ mỗi khi model đổi.

### 7.4 Chaos test (bắt buộc trước mỗi release)
| Kịch bản | Kỳ vọng |
|---|---|
| Kill provider giữa chừng | Fallback đúng, ghi `capability.fallback.triggered` |
| Kill `paosd` giữa Process | Khởi động lại → resume từ checkpoint, không mất event |
| Đầy đĩa | Fail rõ ràng, không hỏng `state.db` |
| Plugin crash | Kernel sống, Process fail có kiểm soát, ghi `plugin.crashed` |
| Mất mạng | Chuyển local, hoàn tất offline |
| Xóa `cache/` giữa chừng | Job vẫn đúng, chỉ chậm hơn |
| Provider trả JSON rác | `INVALID_INPUT` có ngữ cảnh, retry rồi fallback |

### 7.5 Eval suite (kiểm soát chất lượng AI theo thời gian)
```
tests/eval/
├── datasets/script_writing_vi.jsonl     # 30–50 mẫu có nguồn + kỳ vọng
├── rubrics/
└── runs/2026-08-05/report.md
```
Chạy hàng đêm hoặc trước khi đổi prompt/provider. Xuất bảng so sánh:

| Cấu hình | Quality | Edit rate | Cost | Latency |
|---|---|---|---|---|
| qwen2.5-14b + prompt v4 | 84 | 18% | 0₫ | 14s |
| qwen2.5-14b + prompt v5 | 86 | 12% | 0₫ | 15s |
| gpt + prompt v5 | 91 | 9% | 5₫ | 4s |

**Quy tắc regression:** đổi prompt hoặc provider mà quality giảm > 3 điểm hoặc edit_rate tăng > 5% → không merge. Mọi kết quả eval ghi vào `provider_stats`/`prompt_stats` — test cũng là nguồn Operational Knowledge.

## 8. Định nghĩa "Xong" (Definition of Done)

Một tính năng chỉ được coi là xong khi:
- [ ] Có test unit + contract, CI xanh
- [ ] Không vi phạm P1 (CI check `grep` Kernel), P3, P4
- [ ] Có Event tương ứng, đã đăng ký schema
- [ ] Có mã lỗi chuẩn + `hint` khắc phục
- [ ] Có Decision Record nếu có ra quyết định
- [ ] Chạy được ở chế độ offline hoặc fail rõ ràng có hướng dẫn
- [ ] Idempotent + resume được nếu chạy > 60s
- [ ] Đã cập nhật tài liệu trong `docs/`
- [ ] Có mục trong `paosctl explain` nếu ảnh hưởng luồng
