# 13 — Roadmap & Milestones

**Trạng thái:** v1.0

> Nguyên tắc xuyên suốt: **mỗi milestone phải chạy được end-to-end**, không có milestone nào chỉ là "xây hạ tầng". Luôn có thứ dùng được ở cuối mỗi chặng.

---

## Tổng quan

| M | Tên | Thời lượng gợi ý | Kết quả dùng được |
|---|---|---|---|
| M0 | Walking Skeleton | 2 tuần | Chạy 1 job đơn giản end-to-end |
| M1 | Kernel thật | 4 tuần | Process quản lý được, resume được |
| M2 | Capability & Provider | 3 tuần | Đổi provider không sửa code |
| M3 | Agent & Workflow | 4 tuần | Video plugin chạy hoàn chỉnh |
| M4 | Quality & Self-Correction | 2 tuần | Tự sửa, tự chấm điểm |
| M5 | Memory & Knowledge | 4 tuần | Nhớ sở thích, xây KG |
| M6 | Decision Engine | 3 tuần | Tự chọn workflow |
| M7 | Cost / Energy / Time | 2 tuần | Chạy đêm, tiết kiệm, đúng ngân sách |
| M8 | Plugin System & UI | 4 tuần | Cài plugin, có UI |
| M9 | Research Plugin | 3 tuần | Nghiên cứu chủ đề, tích lũy KG thật (UC3) |
| — | Hardening | liên tục | Đạt toàn bộ NFR |

Tổng ≈ 31 tuần lập trình thuần. Với một người làm bán thời gian: **11–15 tháng**.

**Vì sao có M9 dù không có trong bản thảo ban đầu:** doc 01 §3 liệt UC3 (nghiên cứu một chủ đề kỹ thuật, dùng Knowledge Graph + `web.search`) là use case **bắt buộc** của v1. Doc 12 §7 cũng đặt Research là plugin thứ 3 trong lộ trình 4 plugin dùng để tự chứng minh kiến trúc. Không milestone nào ở bản roadmap trước đó build nó — một khoảng trống thật giữa PRD và kế hoạch giao hàng, phát hiện khi rà lại tài liệu (2026-08). M9 lấp khoảng trống đó cho UC3. Excel/Finance (plugin thứ 4 ở doc 12 §7) vẫn hoãn sang v1.x — không UC nào ở doc 01 yêu cầu nó trực tiếp, nó chỉ là bài kiểm tra đa dạng hóa thêm cho P4, có thể làm sau khi v1 đã chạy thật.

---

## M0 — Walking Skeleton (2 tuần)

**Mục tiêu:** đường ống mỏng nhất xuyên qua mọi tầng, chứng minh kiến trúc chạy được.

Phạm vi: `paosd` + `paosctl` tối thiểu · Process 1 task · Event Bus ghi SQLite · 1 capability (`text.generate`) · 1 provider (ollama) · 1 agent giả · trace tối thiểu.

**Exit criteria**
- [ ] `paosctl run "tóm tắt file này"` → chạy → có artifact → `paosctl explain` hiện được trace
- [ ] Event ghi vào DB trước khi dispatch
- [ ] CI grep: Kernel không import SDK AI nào
- [ ] Kill giữa chừng → khởi động lại không hỏng DB

---

## M1 — Kernel thật (4 tuần)

Phạm vi: Process state machine đầy đủ · checkpoint/resume · DAG Scheduler + resource token · Event Bus (delivery, retry, DLQ, replay) · State Store + migration · error taxonomy · idempotency.

**Exit criteria**
- [ ] Chạy 3 Process song song, cancel 1 không ảnh hưởng 2 cái kia
- [ ] `kill -9` giữa Process → khởi động lại resume từ checkpoint, **0 event mất**
- [ ] Task chạy lại 2 lần → 1 bản ghi chi phí (idempotent)
- [ ] Replay event dựng lại được trạng thái phái sinh
- [ ] Đạt PERF-01, PERF-02, PERF-05, REL-01, REL-02

---

## M2 — Capability & Provider (3 tuần)

Phạm vi: Capability schema + registry · Provider adapter + manifest · Router (chưa ranking, chỉ ưu tiên + fallback) · circuit breaker · content-addressed cache · conformance suite · Permission Guard bản đầu + Audit Log · Secret Manager + redaction.

**"Bản đầu" nghĩa là gì cụ thể:** M2 build đủ cơ chế chung của 3 tầng quyền (doc 09 §2) và enforce được 2 hành động CONFIRM tổng quát nhất (ghi ra ngoài `workspace/`, lệnh hệ thống ngoài whitelist). Các hành động CONFIRM còn lại hoàn thiện đúng lúc milestone liên quan cần chúng, không phải nợ bị quên: vượt ngân sách → M7 (Cost Engine) · gửi dữ liệu cá nhân ra cloud → M5 (Privacy Filter) · cài/bật plugin → M8 (duyệt quyền plugin). Permission Guard không có một milestone "hoàn chỉnh" riêng vì nó hoàn chỉnh dần theo đúng nơi mỗi loại rủi ro xuất hiện — ghi rõ ở đây để việc này không bị đọc nhầm thành thiếu sót.

**Exit criteria**
- [ ] 2 provider cho `text.generate`, tắt cái đầu → tự fallback, có Event + Decision Record
- [ ] Chạy lại job giống hệt → cache hit, chi phí 0
- [ ] Provider mới thêm vào chỉ bằng 1 file adapter + 1 YAML
- [ ] Conformance suite chạy trong CI
- [ ] Test quét secret trong log: 0 rò rỉ

---

## M3 — Agent & Workflow (4 tuần)

Phạm vi: Agent Contract 6 bước + SDK · Workflow YAML engine (điều kiện, parallel, retry, compensation) · Video plugin đầu tiên (planning → script → image/voice/subtitle → render) · progress reporting.

**Exit criteria**
- [ ] UC1 chạy hoàn chỉnh **offline 100%**, cho ra file mp4
- [ ] Nhánh song song thật sự tiết kiệm thời gian (đo và ghi vào explain)
- [ ] Thêm Subtitle Agent mới: **0 dòng sửa** Agent cũ (chứng minh P4)
- [ ] Agent gọi capability không khai báo → bị chặn
- [ ] Chạy UC1 với `image.generate` không có provider GPU khả dụng (giả lập không GPU) → workflow vẫn hoàn tất ở chế độ degraded (chậm hơn, dùng biến thể nhẹ hoặc bỏ qua bước ảnh có kiểm soát), không crash, không lỗi âm thầm (LOC-05 — trước M3 không NFR nào từng kiểm điều này cụ thể, đây là lần đầu và duy nhất ở milestone level)

---

## M4 — Quality & Self-Correction (2 tuần)

Phạm vi: Rubric engine (deterministic + LLM judge) · Review Agent · loop có giới hạn · escalation · `edit_rate` tracking · eval harness.

**Exit criteria**
- [x] Script kém bị reject, vòng 2 cải thiện, vòng 3 đổi chiến lược, không lặp vô hạn (P-M4-2, `tests/apps/paosd/test_self_correction.py`)
- [x] Judge dùng provider khác với generator (bị cưỡng chế) (P-M4-2, `Router.exclude_provider`, `test_judge_excludes_generator_provider`)
- [x] Eval suite chạy được, xuất bảng so sánh prompt/provider (P-M4-3, `sdk/eval.py` + `scripts/run_eval.py` + `tests/eval/`)
- [x] Có bản ghi `edit_rate` khi người dùng sửa tay (P-M4-3, `apps/paosd/artifact_store.py::record_edit` + `POST /v1/artifacts/{id}/edited`, `artifact_edits` table)

**M4 hoàn tất — 2026-08-16.**

---

## M5 — Memory & Knowledge (4 tuần)

Phạm vi: 5 tầng memory · retrieval lai · consolidation job hàng đêm · preference learning · Knowledge Graph + extractor · Privacy Filter · `paosctl memory` / `knowledge`.

**Exit criteria**
- [ ] Sau 3 job cùng loại, PAOS không hỏi lại sở thích (duration/tone/voice)
- [ ] Người dùng sửa tay → confidence giảm → hành vi đổi ở job sau
- [ ] KG có ≥ 100 node từ sử dụng thật, mọi edge có provenance
- [ ] Memory L3 không bao giờ rời máy khi `privacy: private` (test đối kháng)
- [ ] Rebuild KG từ replay event cho kết quả tương đương

---

## M6 — Decision Engine (3 tuần)

Phạm vi: feature extraction · candidate + scoring · `decision_outcomes` · Provider Ranking đầy đủ (`provider_stats`, EWMA) · routing policy YAML + profile · `paosctl explain --decisions`.

**Exit criteria**
- [ ] Cùng intent, input khác nhau → chọn workflow khác nhau, giải thích được
- [ ] Sau 20 job, ranking thay đổi theo dữ liệu thực tế (không phải giá trị cứng)
- [ ] Đổi `routing.yaml` sang profile `quality` → hành vi đổi ngay, không cần restart
- [ ] Mọi lựa chọn provider đều có Decision Record

---

## M7 — Cost / Energy / Time (2 tuần)

Phạm vi: Cost Engine (estimate/record/budget 3 tầng) · Energy Engine (GPU/CPU/pin/nhiệt) · Time Engine (cửa sổ thời gian) · báo cáo tiết kiệm.

**Exit criteria**
- [ ] Job vượt ngân sách bị chặn và hỏi, không âm thầm tiêu tiền
- [ ] GPU bận → Process chờ, không chạy đè; rảnh → tự tiếp tục
- [ ] Job nặng gửi lúc 10h sáng → tự chạy 21h, có thông báo
- [ ] Báo cáo tháng: đã tiêu bao nhiêu, tiết kiệm bao nhiêu nhờ local + cache

---

## M8 — Plugin System & UI (4 tuần)

Phạm vi: plugin loader + sandbox subprocess · quyền + duyệt · hot reload · Plugin thứ hai (Document) · Web UI 4 màn hình · export/import workspace.

**Exit criteria**
- [ ] Cài Plugin Document: **0 dòng sửa Kernel** (bằng chứng: diff git)
- [ ] Plugin crash → Kernel sống, có `plugin.crashed`
- [ ] Plugin vượt quyền khai báo → bị chặn, tự disable
- [ ] UI hiển thị Process thời gian thực + explain click được
- [ ] `paosctl export` → mở được toàn bộ bằng tay, không cần PAOS

---

## M9 — Research Plugin (3 tuần)

**Mục tiêu:** hiện thực UC3 (doc 01) — plugin thứ 3 trong lộ trình 4 plugin của doc 12 §7, chứng minh Knowledge Graph và Memory L4 tạo ra giá trị thật từ sử dụng thật, không chỉ tồn tại như schema.

Phạm vi: capability `web.search` + provider (`searxng` local là provider bắt buộc theo ADR-0007; provider cloud là tùy chọn) · Research Agent (workflow `research.topic@1`: tìm kiếm/đọc tài liệu local → tổng hợp → ghi Knowledge Graph có provenance) · Memory L4 (World Cache, TTL) đưa vào dùng thật lần đầu tiên · `paosctl knowledge` mở rộng để truy vấn KG theo chủ đề.

**Exit criteria**
- [ ] UC3 chạy hoàn chỉnh: nghiên cứu 1 chủ đề → KG có node/edge mới, mỗi edge có provenance
- [ ] Hỏi lại một chủ đề liên quan → PAOS dùng lại tri thức đã có trong KG, chỉ ra được nguồn gốc (doc 01 UC3 tiêu chí đạt)
- [ ] Cài Research Plugin: **0 dòng sửa Kernel** (plugin thứ 3 — tiếp nối bằng chứng P4 từ Document ở M8)
- [ ] Memory L4 có TTL hoạt động thật: mục hết hạn không còn được dùng ở truy hồi (doc 07 §1)
- [ ] `web.search` có ít nhất 1 provider local; rút mạng vẫn trả kết quả từ tài liệu local đã có trong Project (P2)

---

## Hardening (liên tục, chốt trước v1.0)

- [ ] Toàn bộ NFR ở doc 11 đạt ngưỡng
- [ ] Chaos suite pass 100%
- [ ] `paosctl doctor` sạch
- [ ] Tài liệu 00–19 khớp với thực tế code (doc 20 là suy đoán, không cần "khớp code" — xem doc 20 §7)
- [ ] Chạy thật 30 ngày liên tục không can thiệp tay
- [ ] Cài đặt từ clone sạch: `git clone` → `pip install -e ".[dev]"` → `paosd` khởi động không lỗi, trên một máy chưa từng chạy PAOS trước đó (không tính máy dev quen thuộc — môi trường "sạch" mới bộc lộ được phụ thuộc ẩn)
- [ ] README §2 Quickstart làm đúng từng bước theo nghĩa đen (không "còn thiếu một bước tôi biết ngầm") → xác nhận "cài đặt = 1 lệnh, gỡ = xóa 1 thư mục" (doc 02 §8, POR-04) là sự thật, không phải khẩu hiệu

### Nghi thức đóng v1.0

Khi toàn bộ checklist trên đạt: `git tag v1.0.0` + một đoạn hồi cứu ngắn ghi vào `docs/retrospective-v1.md` (không phải milestone, không lặp lại mỗi năm như P-YEAR — viết một lần, tại đúng thời điểm này). Nội dung tối thiểu:

1. Chỉ số thành công 12 tháng ở doc 00 §7 — đo được bao nhiêu, cách mục tiêu bao xa
2. Milestone nào chệch ước lượng nhiều nhất, vì sao (dữ liệu cho biết roadmap tương lai nên ước lượng thế nào)
3. Rủi ro nào ở doc 14 thật sự đã xảy ra trong 12 tháng qua
4. Một câu trả lời thật thà theo tinh thần doc 19 PROMPT-CORE mục 8: kiến trúc có đáng công sức bỏ ra không?

Lý do làm việc này thay vì chỉ để checklist tự nói: doc 00 P6 nói tri thức là tài sản, code là chi phí — hồi cứu này là mẩu Operational Knowledge quý nhất của cả dự án, vì nó không dựng lại được sau này (ký ức về việc ước lượng sai ở tháng thứ 3 sẽ phai đi, dữ liệu `decision_outcomes` thì không kể lại được câu chuyện).

---

## Nguyên tắc ưu tiên khi thiếu thời gian

Cắt theo thứ tự này, **không bao giờ cắt ngược**:

1. Cắt phạm vi tính năng (ít plugin hơn, ít capability hơn) ✅
2. Cắt UI (dùng CLI lâu hơn) ✅
3. Cắt tự động hóa nâng cao (Energy/Time Engine hoãn) ✅
4. **KHÔNG cắt:** ranh giới Kernel, Contract, Event Log, Trace, Permission, Test contract ❌

Lý do: nhóm 1–3 thêm vào lúc nào cũng được. Nhóm 4 nếu bỏ qua thì phải viết lại toàn bộ hệ thống — và đó chính xác là cách các dự án 10 năm chết ở năm thứ hai.
