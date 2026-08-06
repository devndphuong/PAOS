# 04 — Core Contracts

**Trạng thái:** v1.0 · **Đây là 4 hợp đồng dài hạn của PAOS. Sửa = phải có ADR + migration + deprecation 2 phiên bản.**

---

## 1. Kernel API

Kernel lộ ra một API duy nhất qua HTTP cục bộ (`127.0.0.1:8787`) + thư viện Python tương ứng. CLI và UI **chỉ** dùng API này — không truy cập DB trực tiếp.

```
POST   /v1/jobs                      → tạo Job, trả về process_id + pid
GET    /v1/processes                 → liệt kê (lọc theo state)
GET    /v1/processes/{pid}           → chi tiết + progress + task tree
POST   /v1/processes/{pid}/pause
POST   /v1/processes/{pid}/resume
POST   /v1/processes/{pid}/cancel
POST   /v1/processes/{pid}/retry     → chạy lại từ checkpoint hoặc từ step chỉ định
GET    /v1/processes/{pid}/trace     → cây trace + decision record
GET    /v1/processes/{pid}/artifacts

GET    /v1/events?since=&type=       → đọc event log
POST   /v1/events/replay             → replay cho subscriber

GET    /v1/capabilities              → danh mục + provider khả dụng
GET    /v1/providers                 → sức khỏe, breaker, thống kê
POST   /v1/providers/{id}/enable|disable

GET    /v1/workflows                 → danh mục workflow
GET    /v1/memory?tier=&q=           → truy vấn memory
GET    /v1/knowledge/graph?node=     → truy vấn KG

GET    /v1/cost/summary?period=      → sổ cái chi phí
GET    /v1/policies | PUT /v1/policies/{name}

POST   /v1/approvals/{id}            → trả lời yêu cầu xác nhận (permission)
GET    /v1/health
```

**Quy ước lỗi (thống nhất toàn hệ thống):**
```json
{"error": {"code": "PROVIDER_DOWN", "message": "...", "retryable": true,
           "context": {"provider_id": "...", "capability": "..."},
           "hint": "Khởi động ComfyUI hoặc bật provider dự phòng",
           "trace_id": "..."}}
```

**Mã lỗi chuẩn:** `INVALID_INPUT · NOT_FOUND · CONFLICT · PERMISSION_DENIED · BUDGET_EXCEEDED · RESOURCE_EXHAUSTED · PROVIDER_DOWN · PROVIDER_TIMEOUT · CONTENT_BLOCKED · QUALITY_BELOW_THRESHOLD · DEPENDENCY_FAILED · CANCELLED · INTERNAL`

## 2. Capability Contract

### 2.1 Định nghĩa
Mỗi Capability là một file trong `capabilities/`:

```yaml
# capabilities/text.generate.v1.yaml
id: text.generate
version: 1
description: Sinh văn bản từ prompt
input_schema:
  type: object
  required: [prompt]
  properties:
    prompt:      {type: string}
    system:      {type: string}
    max_tokens:  {type: integer, default: 2048}
    temperature: {type: number, default: 0.7}
    json_schema: {type: object, description: "Nếu có, output phải khớp schema"}
    task_class:  {type: string, description: "vd script_writing_vi — dùng cho routing & thống kê"}
output_schema:
  type: object
  required: [text]
  properties:
    text:   {type: string}
    usage:  {type: object, properties: {in_tokens: {type: integer}, out_tokens: {type: integer}}}
    meta:   {type: object}
errors: [INVALID_INPUT, PROVIDER_DOWN, PROVIDER_TIMEOUT, CONTENT_BLOCKED, RESOURCE_EXHAUSTED]
idempotent: true
cacheable: true
cache_key_fields: [prompt, system, temperature, max_tokens, json_schema]
```

### 2.2 Provider Adapter phải thực thi

```python
class ProviderAdapter(Protocol):
    manifest: ProviderManifest

    async def health(self) -> Health: ...
    async def estimate(self, capability: str, payload: dict) -> Estimate:
        """Trả về {cost, latency_ms, confidence} — KHÔNG được gọi ra ngoài."""
    async def invoke(self, capability: str, payload: dict, ctx: CallContext) -> dict:
        """Trả về đúng output_schema, hoặc raise ProviderError có mã chuẩn."""
    async def cancel(self, call_id: str) -> None: ...
```

`CallContext` chứa: `call_id, process_id, task_id, deadline, budget_left, privacy_class, cancel_token, on_progress(pct)`.

**Cấm tuyệt đối trong Adapter:** ghi file ngoài `cache/`, phát Event, gọi Capability khác, đọc Memory. Adapter là **driver**, không phải agent.

### 2.3 Bộ test tuân thủ (Conformance Suite)
Provider mới chỉ được đăng ký nếu pass `tests/contract/test_capability_<id>.py`:
- output khớp `output_schema` với 20 input mẫu;
- lỗi trả về đúng mã chuẩn (giả lập timeout, mất mạng, input rác);
- `cancel()` dừng thật trong ≤ 2s;
- `estimate()` sai lệch < 30% so với thực tế trên bộ mẫu;
- không ghi ra ngoài thư mục cho phép (kiểm bằng sandbox fs).

## 3. Agent Contract

### 3.1 Vòng đời bắt buộc 6 bước

```
Initialize() → Validate() → Think() → Execute() → Review() → Publish()
```

| Bước | Trách nhiệm | Được phép | Cấm |
|---|---|---|---|
| `Initialize` | nạp cấu hình, memory liên quan, chuẩn bị context | đọc Memory, đọc Artifact | gọi Capability |
| `Validate` | kiểm tra input đủ & hợp lệ; fail sớm | đọc | mọi side-effect |
| `Think` | lập kế hoạch nội bộ, chọn chiến lược, sinh prompt | gọi Capability nhẹ (rẻ) | ghi Artifact |
| `Execute` | làm việc chính | gọi Capability, ghi Artifact tạm | xóa dữ liệu |
| `Review` | tự kiểm tra kết quả của chính mình | gọi Capability đánh giá | bỏ qua khi lỗi |
| `Publish` | ghi Artifact chính thức + phát Event | ghi Artifact, phát Event | sửa Artifact cũ |

```python
class Agent(Protocol):
    manifest: AgentManifest        # id, version, needs[], produces[], capabilities[], resources[]

    async def initialize(self, ctx: AgentContext) -> None: ...
    async def validate(self, inputs: dict) -> ValidationResult: ...
    async def think(self, inputs: dict) -> Plan: ...
    async def execute(self, plan: Plan) -> ExecResult: ...
    async def review(self, result: ExecResult) -> ReviewResult: ...
    async def publish(self, result: ExecResult) -> list[Artifact]: ...
    async def resume(self, checkpoint: dict) -> ExecResult: ...   # bắt buộc nếu chạy > 60s
```

### 3.2 Agent Manifest
```yaml
id: script.agent
version: 1
needs:    [plan]                       # loại artifact đầu vào
produces: [script]                     # loại artifact đầu ra
capabilities: [text.generate@1]        # chỉ được gọi những cái khai báo ở đây
resources: [cpu_heavy:1]
emits: [script.created, script.rejected]
listens: [plan.created]
quality_rubric: script.rubric@1
max_retries: 3
timeout_sec: 600
checkpointable: true
```

Kernel **cưỡng chế** danh sách `capabilities`: gọi capability không khai báo → `PERMISSION_DENIED`.

### 3.3 Quy tắc bất di bất dịch cho Agent
1. Agent **không được** biết tên provider/model. Vi phạm = PR bị từ chối.
2. Agent **không được** gọi Agent khác. Chỉ phát Event hoặc để Workflow điều phối.
3. Agent phải **idempotent theo `idempotency_key`**.
4. Agent phải trả progress qua `ctx.progress(pct, message)` nếu chạy > 30s.
5. Mọi prompt phải nằm trong file riêng có version (`agents/script/prompts/v3.md`), không nhúng chuỗi dài trong code.

## 4. Workflow Contract (YAML khai báo)

```yaml
id: video.from_pdf
version: 2
description: PDF → video ngắn
inputs:
  pdf:      {type: file, mime: application/pdf, required: true}
  duration: {type: int, default: 75}
policy:
  budget: {max: 20, currency: JPY}
  quality_gate: {min_score: 80, max_loops: 2}
  privacy: private

steps:
  - id: detect
    kind: capability
    ref: doc.parse@1
    with: {file: "${inputs.pdf}"}

  - id: ocr
    kind: capability
    ref: doc.ocr@1
    when: "${steps.detect.output.has_text_layer == false}"
    with: {file: "${inputs.pdf}"}
    resources: [cpu_heavy:1]

  - id: plan
    kind: agent
    ref: planning.agent@1
    with: {text: "${steps.ocr.output.text ?? steps.detect.output.text}"}

  - id: script
    kind: agent
    ref: script.agent@1
    with: {plan: "${steps.plan.output}"}

  - id: review_script
    kind: agent
    ref: review.agent@1
    with: {artifact: "${steps.script.output}", rubric: script.rubric@1}
    on_fail:
      goto: script                     # self-correction loop
      max_loops: 2
      carry: {feedback: "${steps.review_script.output.feedback}"}

  - id: media                          # nhánh song song
    kind: parallel
    steps:
      - {id: images,   kind: agent, ref: image.agent@1,    with: {script: "${steps.script.output}"}, resources: [gpu:1]}
      - {id: voice,    kind: agent, ref: voice.agent@1,    with: {script: "${steps.script.output}"}}
      - {id: subtitle, kind: agent, ref: subtitle.agent@1, with: {script: "${steps.script.output}"}}

  - id: render
    kind: agent
    ref: render.agent@1
    with: {images: "${steps.media.images.output}", voice: "${steps.media.voice.output}",
           subtitle: "${steps.media.subtitle.output}"}
    resources: [cpu_heavy:2]
    retry: {max: 2}

  - id: final_review
    kind: agent
    ref: review.agent@1
    with: {artifact: "${steps.render.output}", rubric: video.rubric@1}

outputs:
  video: "${steps.render.output.path}"

on_error:
  compensate: [cleanup_temp]
  notify: true
```

**Đặc tính bắt buộc của Workflow Engine:**
- Biểu thức `${...}` chỉ đọc dữ liệu, **không có eval code tùy ý** (sandbox: chỉ so sánh, `??`, truy cập trường).
- Mọi vòng lặp phải có `max_loops`. Không có vòng lặp vô hạn.
- `parallel` chỉ chạy song song trong giới hạn Resource Token.
- DAG được resolve và **đóng băng** vào `projects/<x>/workflow.json` khi Process bắt đầu → tái lập được y hệt sau này.

## 5. Plugin Manifest

```yaml
id: paos.plugin.video
name: Video Production
version: 1.2.0
paos_api: ">=1.0,<2.0"
provides:
  agents:       [planning.agent@1, script.agent@1, image.agent@1, voice.agent@1, render.agent@1]
  workflows:    [video.from_pdf@2, video.from_topic@1]
  capabilities: []
  providers:    [comfyui.flux@1, edge_tts@1]
  rubrics:      [script.rubric@1, video.rubric@1]
permissions:
  fs:      {read: ["projects/*", "models/*"], write: ["projects/*/", "cache/"]}
  network: {allow: ["localhost:8188", "localhost:11434"]}
  exec:    ["ffmpeg"]
  spend:   {max_per_job: 20, currency: JPY}
runtime: {type: process, entry: "python -m paos_video.main", isolation: subprocess}
signature: null
```

Cài plugin = ghi manifest vào Registry + phát `plugin.installed`. **Kernel không thay đổi một dòng.**

## 6. Bảng tương thích phiên bản

| Hợp đồng | Cách đánh version | Quy tắc phá vỡ |
|---|---|---|
| Kernel API | prefix `/v1` | chỉ tăng khi có breaking change; hỗ trợ song song ≥ 6 tháng |
| Capability | `id@N` | thêm field optional = không tăng; đổi/xóa field = tăng N |
| Agent | `id@N` | đổi `needs`/`produces` = tăng N |
| Event | field `version` | thêm field optional = không tăng |
| Workflow | `id@N` | đóng băng bản đã chạy, không sửa ngược |
