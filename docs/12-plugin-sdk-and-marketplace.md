# 12 — Plugin SDK & Skill Marketplace

**Trạng thái:** v1.0 (thiết kế), triển khai ở M6

> Ngày mai thêm Plugin Video. Ngày kia Plugin Excel. Ngày kia nữa Plugin Finance.
> **Không cần sửa Kernel.** Nếu phải sửa Kernel để thêm plugin → kiến trúc đã sai, không phải plugin sai.

---

## 1. Plugin là gì

Một gói đóng gói bất kỳ tổ hợp nào của: **Agent · Workflow · Capability · Provider · Rubric · Template · Prompt · UI panel**.

```
plugins/paos-video/
├── plugin.yaml              # manifest (doc 04 §5)
├── agents/
│   ├── planning/{agent.py, manifest.yaml, prompts/v1.md, v2.md}
│   ├── script/
│   └── render/
├── workflows/video.from_pdf.v2.yaml
├── providers/comfyui_flux.py
├── rubrics/script.rubric.v1.yaml
├── templates/
├── tests/                   # BẮT BUỘC: conformance + golden
└── README.md
```

## 2. Vòng đời Plugin

```
discover → validate manifest → kiểm tra tương thích paos_api
   → hiển thị quyền yêu cầu → NGƯỜI DÙNG DUYỆT (tier CONFIRM)
   → install (ghi Registry) → phát plugin.installed
   → Registry hot-reload → Agent/Workflow/Provider mới khả dụng ngay
   → disable / uninstall (giữ dữ liệu Project, chỉ gỡ code)
```

Gỡ plugin **không được** làm mất dữ liệu đã sinh ra. Artifact và Project luôn thuộc về người dùng, không thuộc về plugin.

## 3. SDK cho người viết plugin

```python
from paos.sdk import Agent, AgentContext, capability, emits, listens

class ScriptAgent(Agent):
    id = "script.agent"; version = 1
    needs = ["plan"]; produces = ["script"]
    capabilities = ["text.generate@1"]      # Kernel cưỡng chế danh sách này
    quality_rubric = "script.rubric@1"

    async def validate(self, inputs):
        return self.ok() if inputs.get("plan") else self.fail("MISSING_PLAN")

    async def think(self, inputs):
        prefs = await self.memory.get_preferences(["tone", "video.duration_sec"])
        prompt = self.prompt("v5").render(plan=inputs["plan"], **prefs)
        return self.plan(prompt=prompt, task_class="script_writing_vi")

    async def execute(self, plan):
        # KHÔNG biết provider nào sẽ phục vụ — đó là việc của Capability Router
        out = await self.call("text.generate@1", {
            "prompt": plan.prompt, "max_tokens": 1200, "task_class": plan.task_class})
        return self.result(text=out["text"])

    @emits("script.created")
    async def publish(self, result):
        return [await self.write_artifact("script", "script.json", result.text)]
```

SDK cung cấp: `self.call()` (capability), `self.memory` (đọc theo quyền), `self.prompt()` (prompt có version), `self.write_artifact()`, `self.progress()`, `self.checkpoint()`, `self.ok/fail()`, và context chứa `deadline`, `budget_left`, `cancel_token`.

**SDK cố tình KHÔNG cung cấp:** HTTP client tới vendor, tên model, đường dẫn tuyệt đối, truy cập DB. Nếu bạn thấy cần chúng → bạn đang viết Provider, không phải Agent.

## 4. Provider Plugin

```python
from paos.sdk import ProviderAdapter, ProviderError

class ComfyUIFlux(ProviderAdapter):
    manifest = load("provider.yaml")

    async def health(self):
        return await self.http_get("/system_stats", timeout=3)

    async def estimate(self, capability, payload):
        return {"cost": 0, "latency_ms": 180_000 * payload.get("count", 1), "confidence": 0.7}

    async def invoke(self, capability, payload, ctx):
        try:
            job = await self.submit_workflow(payload)
            async for pct in self.poll(job):
                ctx.progress(pct)                 # bắt buộc nếu chạy > 30s
                if ctx.cancelled: await self.abort(job); raise ProviderError("CANCELLED")
            return {"artifacts": [...], "meta": {"seed": job.seed}}
        except OutOfMemory as e:
            raise ProviderError("RESOURCE_EXHAUSTED", retryable=True,
                                hint="Giảm batch size hoặc độ phân giải") from e
```

## 5. Điều kiện để một Plugin được chấp nhận

- [ ] Manifest hợp lệ, khai báo `paos_api` range
- [ ] Quyền yêu cầu **tối thiểu cần thiết** (least privilege), có giải thích từng quyền
- [ ] Mọi Agent pass Agent Conformance Suite
- [ ] Mọi Provider pass Capability Conformance Suite
- [ ] Có ít nhất 1 golden test với fixture
- [ ] Không import trực tiếp `kernel.*` (chỉ dùng `paos.sdk`)
- [ ] Có README: làm gì, cần gì, chi phí ước tính, giới hạn đã biết
- [ ] Prompt để trong file riêng có version, không nhúng chuỗi dài trong code

## 6. Marketplace (v2+)

Giai đoạn 1 (v1): plugin cài từ thư mục local hoặc git URL.
Giai đoạn 2: registry index dạng file JSON tĩnh (có thể host trên git) + `paosctl plugin search|install`.
Giai đoạn 3: chữ ký số, xếp hạng dựa trên **dữ liệu vận hành thực tế của chính bạn** (quality, edit_rate, tỉ lệ lỗi) chứ không phải sao đánh giá.

**Nguyên tắc chống lệ thuộc:** marketplace không bao giờ được trở thành thành phần bắt buộc. PAOS phải chạy đầy đủ khi marketplace offline hoặc biến mất vĩnh viễn.

## 7. Lộ trình plugin đầu tiên (tự dùng để chứng minh kiến trúc)

| Plugin | Chứng minh điều gì | Milestone |
|---|---|---|
| **Video** (đầu tiên) | Workflow phức tạp, song song, self-correction, đa capability | M3 |
| **Document** | Decision Engine chọn nhánh (OCR hay không), chuỗi xử lý dài | M8 |
| **Research** | Knowledge Graph, memory L4, web search | M9 |
| **Excel/Finance** | Capability hoàn toàn mới, chứng minh Kernel thật sự trung lập | v1.x (sau Hardening — chưa lên lịch, không UC nào ở doc 01 yêu cầu nó trực tiếp) |

Nếu plugin thứ 4 cài được mà **0 dòng sửa Kernel**, kiến trúc đã đạt mục tiêu 10 năm. Đây là bài kiểm tra cuối cùng của toàn bộ dự án — nhưng nó nằm ngoài phạm vi cam kết của v1 (xem doc 13). Ba plugin đầu (Video, Document, Research) đã đủ để chứng minh P4 với cường độ tăng dần trong v1; Excel/Finance là xác nhận bổ sung cho giai đoạn sau, không phải điều kiện để coi v1 hoàn thành.
