# 02 — System Architecture Document (SAD)

**Trạng thái:** v1.0 · **Thay đổi lớn cần ADR**

---

## 1. Sơ đồ tầng (Layered View)

```
┌──────────────────────────────────────────────────────────────┐
│ L9  INTERFACE       CLI (paosctl) · Local Web UI · Scheduler │
│                     hooks · Watch folder                      │
├──────────────────────────────────────────────────────────────┤
│ L8  PLANNER         Nhận ý định người dùng → Job Spec         │
├──────────────────────────────────────────────────────────────┤
│ L7  DECISION ENGINE Chọn Workflow · Chọn chiến lược · Ước     │
│                     lượng chi phí · Sinh Decision Record      │
├──────────────────────────────────────────────────────────────┤
│ L6  WORKFLOW ENGINE Diễn giải DAG · điều kiện · vòng lặp có   │
│                     giới hạn · compensation                   │
├──────────────────────────────────────────────────────────────┤
│ L5  AGENT SYSTEM    Agent tuân Agent Contract · Review Agent  │
├──────────────────────────────────────────────────────────────┤
│ L4  CAPABILITY      Interface trừu tượng: text.generate,      │
│                     image.generate, audio.tts, doc.ocr...     │
├──────────────────────────────────────────────────────────────┤
│ L3  PROVIDER        Adapter: ollama · comfyui · openai ·      │
│                     anthropic · edge-tts · ffmpeg · tesseract │
├──────────────────────────────────────────────────────────────┤
│ L2  RESOURCE        GPU/CPU/RAM/Net token · rate limit ·      │
│                     circuit breaker                           │
├──────────────────────────────────────────────────────────────┤
│ L1  KERNEL          Process · Scheduler · Event Bus · State · │
│                     Registry · Policy · Permission            │
├──────────────────────────────────────────────────────────────┤
│ L0  STORAGE         SQLite (state, events, KG, vector) ·      │
│                     Filesystem (Workspace)                    │
└──────────────────────────────────────────────────────────────┘
```

**Quy tắc gọi:** một tầng chỉ được gọi xuống tầng liền kề hoặc thấp hơn, **không bao giờ gọi lên**. Muốn "gọi lên" thì phát Event.

## 2. Luồng chính (end-to-end)

```
User: "Làm video 60s về MongoDB từ file này.pdf"
  │
  ▼
[L8 Planner] ──► JobSpec {intent, inputs, constraints, preferences}
  │                       │
  │                       └── nạp Preference từ L2 Memory (tone, độ dài, giọng)
  ▼
[L7 Decision Engine]
  ├─ phân tích input: PDF? có ảnh? có text layer? ngôn ngữ?
  ├─ chọn workflow: video.from_pdf@2  (vì cần OCR)
  ├─ ước lượng: 7 task · ~9 phút · ~0₫ (all-local) 
  └─ ghi DecisionRecord{options[], scores[], chosen, rationale}
  │
  ▼
[L1 Kernel] tạo Process PID=1001, state=PENDING, ghi checkpoint 0
  │
  ▼
[L1 Scheduler] duyệt DAG, cấp Resource Token
  │
  ▼
[L6 Workflow Engine] điều phối từng Task
  │
  ├──► [L5 Agent: OCR]      ──► [L4 doc.ocr]        ──► [L3 tesseract]
  ├──► [L5 Agent: Planning] ──► [L4 text.generate]  ──► [L3 ollama/qwen]
  ├──► [L5 Agent: Script]   ──► [L4 text.generate]  ──► [L3 ollama/qwen]
  ├──► [L5 Review Agent]  ← quality gate, có thể quay lại Script
  ├──╥► [L5 Agent: Image]   ──► [L4 image.generate] ──► [L3 comfyui]   ┐ chạy
  │  ╠► [L5 Agent: Voice]   ──► [L4 audio.tts]      ──► [L3 edge-tts]  │ song
  │  ╚► [L5 Agent: Subtitle]──► [L4 text.transform] ──► [L3 local]     ┘ song
  └──► [L5 Agent: Render]   ──► [L4 video.render]   ──► [L3 ffmpeg]
  │
  ▼ mỗi bước phát Event → Event Bus → Memory Writer, Cost Ledger, Trace, UI
  ▼
[Publish] artifact vào Project/ · cập nhật Knowledge Graph · Process = SUCCEEDED
```

## 3. Kernel (L1) — chi tiết

Kernel gồm đúng **7 module**, không hơn:

### 3.1 Process Manager
Sở hữu vòng đời Process. Máy trạng thái:

```
        ┌──────────────────────────────────────────┐
        ▼                                          │
CREATED → PLANNING → QUEUED → RUNNING ──► SUCCEEDED│
                        ▲        │                 │
                        │        ├──► WAITING ──────┘  (chờ resource / chờ người)
                        │        ├──► PAUSED ──────────► (resume)
                        │        ├──► FAILED ──► COMPENSATING ──► FAILED_FINAL
                        └────────┴──► CANCELLED
```

Chuyển trạng thái hợp lệ được định nghĩa cứng trong bảng; mọi chuyển trạng thái đều phát Event và ghi vào `process_transitions`.

### 3.2 Scheduler
- Duyệt DAG theo topological order, chạy song song tối đa `max_parallel` **có xét Resource Token**.
- Resource Token (semaphore): `gpu:1`, `cpu_heavy:2`, `net_api:4`, `disk_io:2`. Task khai báo mình cần token nào.
- Ưu tiên: `priority (0-9)` → `deadline` → `FIFO`.
- **Backpressure:** nếu hàng đợi > ngưỡng, Job mới vào QUEUED thay vì RUNNING.
- **Time window:** hỏi Time Engine trước khi cấp phát (xem doc 06).
- Cơ chế tiết kiệm thời gian điển hình: Planning xong → Image (3 phút), Voice (20 giây), Subtitle (5 giây) chạy song song → tổng = max(3ph) thay vì tổng cộng dồn.

### 3.3 Event Bus
- **Durable-first:** ghi event vào SQLite (`events`) **trước** khi dispatch. Không mất event kể cả khi crash giữa chừng.
- Giao hàng **at-least-once** → mọi subscriber phải idempotent theo `event_id`.
- Subscriber đăng ký theo pattern: `agent.*.completed`, `capability.call.failed`.
- Dead Letter Queue cho subscriber lỗi quá `max_retries`.
- Replay: `paosctl events replay --from <ts> --to <ts> --to-subscriber X` (dùng để rebuild Memory/KG).

### 3.4 State Store
SQLite chế độ WAL, một file `workspace/.paos/state.db`. Ghi checkpoint là một transaction. Xem schema ở doc 03.

### 3.5 Registry
Đăng ký & tra cứu: Capability, Provider, Agent, Workflow, Plugin. Load lúc khởi động + hot-reload khi có Event `plugin.installed`.

### 3.6 Policy Engine
Đánh giá các policy khai báo (YAML): routing weight, budget, privacy class, time window, permission tier. **Policy là dữ liệu, không phải code** — sửa policy không cần deploy.

### 3.7 Permission Guard
Chặn mọi tác vụ có side-effect ngoài Workspace. Xem doc 09.

**Kernel KHÔNG chứa:** prompt, tên model, logic nghiệp vụ video/PDF, HTTP client tới vendor, tokenizer.

## 4. Capability Layer (L4) — trái tim của tính bền vững

Capability là một **interface có phiên bản**, được định nghĩa bằng JSON Schema:

```
capability: image.generate@1
  input:  {prompt: str, negative?: str, size: {w,h}, seed?: int, style?: str}
  output: {artifacts: [{path, mime, w, h}], meta: {seed, provider_hint?}}
  errors: [INVALID_PROMPT, CONTENT_BLOCKED, RESOURCE_EXHAUSTED, PROVIDER_DOWN]
```

Danh mục Capability lõi v1:

| Capability | Provider ví dụ |
|---|---|
| `text.generate` | ollama/qwen, ollama/deepseek, openai, anthropic |
| `text.embed` | bge-m3 local, openai |
| `text.transform` | local (rule + LLM) |
| `image.generate` | comfyui/flux, gpt-image |
| `image.edit` | comfyui |
| `audio.tts` | edge-tts, piper, elevenlabs |
| `audio.stt` | whisper.cpp |
| `doc.ocr` | tesseract, paddleocr, vision model |
| `doc.parse` | pymupdf, docling |
| `video.render` | ffmpeg |
| `web.search` | searxng local, brave api |
| `file.convert` | pandoc, ffmpeg |

**Quy tắc:** Agent chỉ nói `need: image.generate`. Capability Router tìm provider theo chuỗi fallback:

```
Local Flux → (lỗi) → ComfyUI khác → (lỗi) → GPT Image → (lỗi) → FAIL rõ ràng
```

Agent hoàn toàn không biết ai đã phục vụ mình.

## 5. Provider Layer (L3)

Mỗi Provider là một Adapter thực thi Capability Contract + khai báo metadata:

```yaml
id: ollama.qwen2.5-14b
implements: [text.generate@1, text.transform@1]
class: local            # local | cloud | hybrid
privacy: private        # private | shared | public
cost: {unit: token, in: 0, out: 0, currency: JPY}
limits: {ctx: 32768, rpm: null, concurrent: 1}
resources: [gpu:1]
health_check: {type: http, url: "http://localhost:11434/api/tags", interval: 60}
quality_hint: {default: 82}
```

**Circuit breaker:** 3 lỗi liên tiếp → OPEN 60s → HALF_OPEN thử 1 lần → CLOSED. Trạng thái breaker ghi vào Registry và hiện trong UI.

## 6. Agent System (L5)

Mọi Agent đều có cùng vòng đời (chi tiết ở doc 04):

```
Initialize() → Validate() → Think() → Execute() → Review() → Publish()
```

Nhờ đồng nhất, có thể thay bất kỳ Agent nào bất kỳ lúc nào, và Kernel có thể chèn hook (đo thời gian, chi phí, quality) ở mọi bước mà Agent không biết.

**Review Agent** là công dân hạng nhất, không phải phụ kiện: Script xong **không render ngay**; Review Agent kiểm tra logic, trùng ý, độ dài, CTA, Hook. Không đạt → quay lại Script Agent kèm feedback có cấu trúc. Đây là **Self-Correction Loop** với `max_retries` và ngân sách retry riêng.

## 7. Event-driven Extensibility

```
Planning Agent ──► phát Event: plan.created
                        │
        ┌───────────────┼──────────────────┬─────────────────┐
        ▼               ▼                  ▼                 ▼
   Script Agent    Memory Writer     Cost Ledger      Thumbnail Agent
                                                      (thêm sau, 0 dòng sửa Planning)
```

## 8. Mô hình triển khai (Deployment)

```
Máy cá nhân
├── paosd (daemon)          — Kernel + HTTP API cục bộ (127.0.0.1:8787)
├── paosctl (CLI)           — nói chuyện với paosd
├── Web UI                  — tĩnh, gọi paosd
├── Provider process
│   ├── ollama              (localhost:11434)
│   ├── comfyui             (localhost:8188)
│   └── plugin process      — JSON-RPC qua stdio, sandbox
└── workspace/              — toàn bộ dữ liệu người dùng
```

**Không** dùng Docker bắt buộc, **không** message broker ngoài, **không** cloud DB. Cài đặt = 1 lệnh, gỡ = xóa 1 thư mục.

## 9. Chiến lược tiến hóa & phiên bản

- Mọi bản ghi có `schema_version`. Migration là script có thứ tự, chạy tự động, có backup trước khi chạy.
- Capability đánh version trong id (`@1`, `@2`). Provider có thể implement nhiều version.
- Deprecate theo 3 pha: `active → deprecated (cảnh báo) → removed` (tối thiểu 2 minor version giữa mỗi pha).
- **Bài kiểm tra 10 năm:** mỗi quý, chạy `tests/contract/` với toàn bộ provider bị thay bằng stub. Nếu pass → kiến trúc còn khỏe.

## 10. Các quyết định đã chốt (tham chiếu ADR)

| Chủ đề | Quyết định | ADR |
|---|---|---|
| Ngôn ngữ Kernel | Python 3.12 + asyncio | ADR-0001 |
| Lưu trạng thái | SQLite (WAL) + file JSON | ADR-0002 |
| Giao tiếp nội bộ | Event Bus bền vững, không gọi trực tiếp | ADR-0003 |
| Truy cập AI | Chỉ qua Capability, không gọi thẳng | ADR-0004 |
| Cô lập Plugin | Tiến trình riêng, JSON-RPC stdio | ADR-0005 |
| Định nghĩa Workflow | YAML khai báo, không code | ADR-0006 |
| Mặc định vận hành | Local-first, offline-capable | ADR-0007 |
| Kiểm định chất lượng | Deterministic check + LLM-as-judge | ADR-0008 |
| Ranh giới Kernel | Cấm mọi phụ thuộc AI SDK | ADR-0009 |
| Xóa dữ liệu | Trash, không xóa cứng | ADR-0012 |
