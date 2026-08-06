# 10 — Observability & Explainability

**Trạng thái:** v1.0

> Không chỉ đưa ra kết quả. Mà phải trả lời được: **Tại sao chọn GPT? Prompt gì? Workflow nào? Agent nào? Mất bao lâu? Tốn bao nhiêu?**
> Nếu không trả lời được, hệ thống chưa hoàn thành — dù kết quả có đẹp đến đâu.

---

## 1. Ba trụ cột

| Trụ cột | Câu hỏi trả lời | Cơ chế |
|---|---|---|
| **Trace** | Chuyện gì đã xảy ra, theo thứ tự nào, mất bao lâu? | Span cây, lồng nhau |
| **Decision Record** | Vì sao hệ thống chọn phương án đó? | Bản ghi có ứng viên + điểm số |
| **Ledger** | Tốn bao nhiêu, ở đâu? | Sổ cái chi phí + tài nguyên |

Cả ba đều dựng từ Event Log — **một nguồn sự thật duy nhất**.

## 2. Trace Model

```json
{
  "trace_id": "proc_01J8ZQ...",
  "spans": [
    {"span_id": "s1", "parent": null, "name": "process:video.from_pdf@2",
     "start": "...", "dur_ms": 512340, "status": "ok",
     "attrs": {"pid": 1001, "cost": 0, "quality": 89}},
    {"span_id": "s2", "parent": "s1", "name": "task:ocr", "dur_ms": 41200,
     "attrs": {"provider": "tesseract", "pages": 32}},
    {"span_id": "s3", "parent": "s1", "name": "agent:script.agent@1", "dur_ms": 18400,
     "attrs": {"prompt_version": "v5", "loops": 2}},
    {"span_id": "s4", "parent": "s3", "name": "capability:text.generate@1", "dur_ms": 14100,
     "attrs": {"provider": "ollama.qwen2.5-14b", "in_tok": 2140, "out_tok": 612,
               "cost": 0, "cache": "miss", "decision_id": "dec_01J8ZT..."}}
  ]
}
```

Quy ước tên span: `process:<workflow>` · `task:<step_id>` · `agent:<id>` · `capability:<id>` · `provider:<id>` · `review:<rubric>` · `wait:<resource>`.

Định dạng tương thích khái niệm OpenTelemetry (trace/span/attrs) nhưng lưu cục bộ, **không phụ thuộc backend ngoài**. Xuất OTLP là tùy chọn.

## 3. `paosctl explain` — giao diện giải thích

```
$ paosctl explain 1001

PROCESS 1001  RenderVideo: MongoDB          SUCCEEDED  8m32s   0₫   Q:89

WORKFLOW  video.from_pdf@2
  └─ vì sao? PDF không có text layer (32 ảnh) → cần OCR.
     Ứng viên: video.from_pdf@2 (0.91) · doc.summarize@1 (loại: intent≠summarize)
     Kinh nghiệm: 14 job tương tự, 13 thành công, Q trung bình 87        [dec_01J8ZT]

TASKS
  ✓ detect        1.2s    doc.parse@1     pymupdf
  ✓ ocr          41.2s    doc.ocr@1       tesseract          [chờ cpu_heavy 3.1s]
  ✓ plan         12.8s    text.generate   ollama/qwen2.5-14b  0₫   prompt plan/v3
  ✓ script       18.4s    text.generate   ollama/qwen2.5-14b  0₫   prompt script/v5  (2 vòng)
      ↳ vòng 1: Q=68 REJECT — hook yếu ("mở đầu định nghĩa khô khan")
      ↳ vòng 2: Q=91 PASS
  ✓ media (song song, tiết kiệm 3m41s so với tuần tự)
      ├─ images   3m02s   image.generate  comfyui/flux       0₫   [giữ gpu:1]
      ├─ voice     0m21s   audio.tts       edge-tts           0₫
      └─ subtitle  0m05s   text.transform  local              0₫
  ✓ render       2m10s    video.render    ffmpeg             0₫
  ✓ final_review 14.0s    text.generate   ollama/qwen2.5-14b  Q=89 PASS

QUYẾT ĐỊNH ĐÁNG CHÚ Ý
  • Không gọi GPT cho script: qwen đạt Q=82 ≥ ngưỡng 80, chi phí 0 thắng ở trọng số
    w_c=0.35. Nếu bật profile 'quality' thì GPT sẽ được chọn.                [dec_01J8ZU]
  • Fallback: comfyui lỗi 1 lần (CUDA OOM) → retry sau 4s với batch nhỏ hơn → thành công.

CHI PHÍ    0₫ (ước lượng 0₫)   ·  Cache hit 2/9 (tiết kiệm ~7₫)
TÀI NGUYÊN gpu:1 giữ 3m02s  ·  cpu_heavy đỉnh 2/2  ·  chờ tài nguyên tổng 3.1s
BỘ NHỚ     đã dùng 3 sở thích: duration=75s, tone=professional, voice=female
TRI THỨC   +2 node KG (MongoDB, Sharding) · +3 edge

Xem thêm:  paosctl explain 1001 --step script --show-prompt
           paosctl explain 1001 --decisions
           paosctl trace 1001 --format json
```

**Yêu cầu bắt buộc:** mọi thông tin trên phải dựng được **hoàn toàn từ Event Log**, không cần Process còn đang chạy, và còn dùng được sau 2 năm.

## 4. Decision Record ở khắp nơi

Bốn nơi **bắt buộc** phải ghi Decision Record:
1. Chọn Workflow
2. Chọn Provider (mỗi capability call)
3. Quyết định retry / escalate / đổi chiến lược
4. Hoãn vì tài nguyên, thời gian hoặc ngân sách

Nguyên tắc viết `rationale`: một câu, nêu **yếu tố quyết định**, không liệt kê lại toàn bộ. Sai: "đã cân nhắc nhiều yếu tố". Đúng: "qwen đủ ngưỡng chất lượng và chi phí 0 thắng ở trọng số cost=0.35".

## 5. Logging

```
Cấp độ:  DEBUG (dev) · INFO (mốc) · WARN (bất thường tự phục hồi) · ERROR (task fail) · FATAL (kernel chết)
Định dạng: JSON dòng (ndjson), có trace_id + process_id + task_id ở MỌI dòng
Nơi lưu:  .paos/logs/paosd.ndjson (xoay vòng 100MB × 10) + projects/<x>/logs/
```
Redaction chạy trước khi ghi (doc 09 §6). Không log payload > 4KB — thay bằng `artifact_id`.

## 6. Chỉ số (Metrics)

| Nhóm | Chỉ số |
|---|---|
| Process | số đang chạy/chờ/thất bại, thời lượng p50/p95, tỉ lệ resume thành công |
| Task | tỉ lệ retry, tỉ lệ fail theo mã lỗi, thời gian chờ tài nguyên |
| Provider | latency p50/p95, tỉ lệ lỗi, số lần breaker mở, tỉ lệ được chọn |
| Quality | điểm trung bình theo loại artifact, tỉ lệ reject vòng 1, **edit_rate** |
| Cost | chi phí ngày/tháng, % cache hit, tiền tiết kiệm nhờ cache và nhờ local |
| Knowledge | số node/edge mới/tuần, số preference đã học, số playbook lỗi |

Lưu dưới dạng bảng tổng hợp trong SQLite (rollup hàng giờ). **Không** cần Prometheus/Grafana ở v1 — thêm CSV export là đủ.

## 7. Sức khỏe hệ thống

```
$ paosctl doctor
✓ Kernel        v1.2.0, uptime 4d 2h
✓ Database      12.4MB, integrity OK, backup gần nhất 6h trước
✓ Disk          workspace 41GB / còn trống 180GB
⚠ Provider      comfyui.flux  DEGRADED (2 lỗi CUDA OOM trong 1h)
✓ Provider      ollama.qwen2.5-14b  OK (p50 14.1s)
✗ Provider      openai.gpt  DISABLED (không có API key)
✓ Event Bus     0 dead letter, độ trễ dispatch p95 8ms
⚠ Artifact      3 file mồ côi trong projects/ (dùng --fix để dọn)
✓ Memory        L3: 47 mục · KG: 312 node / 508 edge
```

## 8. Giao diện người dùng tối thiểu (UI v1)

Bốn màn hình, không hơn:
1. **Processes** — bảng như Task Manager: PID, tên, trạng thái, tiến độ, thời gian, chi phí; nút pause/cancel/explain.
2. **Explain** — cây trace tương tác, click vào bước để xem prompt/input/output/decision.
3. **Projects** — duyệt file, xem artifact, so sánh phiên bản (`supersedes`), khôi phục.
4. **Knowledge** — xem sở thích đã học, đồ thị tri thức, playbook vận hành, và **nút quên**.

Nguyên tắc UI: mọi con số hiển thị đều phải click được để xem *vì sao*. Không có con số mồ côi.
