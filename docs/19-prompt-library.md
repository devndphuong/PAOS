# 19 — Prompt Library: từ Ngày 0 đến v1.0

**Trạng thái:** Draft v0.1 · **Phạm vi:** toàn bộ vòng đời dự án (≈ 10–14 tháng)

> Tài liệu này là **công cụ làm việc**, không phải hợp đồng. Nếu nó mâu thuẫn với doc 00–17, các doc kia thắng.

---

## §0 · Cách dùng

### 0.1 Thiết lập một lần

1. Tạo một Project (hoặc thư mục ngữ cảnh) chứa **toàn bộ doc 00–19**.
2. Dán **§1 PROMPT-CORE** vào phần chỉ dẫn thường trực của Project. Đây là thứ giữ cho trợ lý không trôi dạt qua hàng trăm phiên.
3. Mỗi phiên làm việc = mở hội thoại mới + dán **một** prompt từ §3–§5.

### 0.2 Nguyên tắc chia prompt

| Nguyên tắc | Nghĩa là |
|---|---|
| **Một prompt = một lát cắt = một commit** | Nếu một prompt tạo ra 2 commit không liên quan, nó đã quá to. Tách ra |
| **Ngữ cảnh nằm ở PROMPT-CORE, không lặp lại** | Prompt lát cắt chỉ nói *việc này*, không nói lại nguyên tắc dự án |
| **Contract trước, code sau** | Mọi prompt xây dựng đều có 2 pha bắt buộc: trình bày thiết kế → **dừng chờ duyệt** → mới viết code |
| **Kết phiên có nghi thức** | Luôn đóng bằng P-CLOSE. Không đóng máy giữa chừng mà không chạy nó |

### 0.3 Bản đồ prompt

```
§1  PROMPT-CORE            — dán một lần, dùng mãi
§2  Prompt vòng lặp        — 10 prompt dùng lại mỗi ngày
§3  Ngày 0                 — 3 prompt
§4  M0 Walking Skeleton    — 6 prompt
§5  M1 → M8 + Hardening    — 35 prompt
§6  Nhịp định kỳ           — tuần / milestone / quý / năm
§7  Prompt cấm             — những gì không bao giờ nhờ trợ lý làm
```

---

## §1 · PROMPT-CORE — khối ngữ cảnh thường trực

> Dán nguyên khối này vào **Project Instructions**. Không dán lại ở mỗi phiên.
> Đọc lại và cập nhật nó vào cuối mỗi milestone.

```
Bạn đang cộng tác với tôi trên PAOS — một Personal AI Operating System chạy
local-first, do MỘT người phát triển trong 10–14 tháng, thiết kế để sống 10 năm.
Toàn bộ đặc tả nằm ở doc 00–19 trong Project. Chúng là nguồn sự thật.

═══ VAI TRÒ CỦA BẠN ═══
Bạn là kỹ sư đồng hành có tính kỷ luật kiến trúc cao hơn tôi. Nhiệm vụ của bạn
KHÔNG phải là giúp tôi đi nhanh nhất, mà là giúp tôi đi theo cách mà "tôi của
hai năm sau" còn hiểu được và còn sửa được.

Khi tôi yêu cầu một thứ vi phạm ranh giới kiến trúc, bạn phải nói ra — kể cả
khi tôi đang vội, kể cả khi tôi có vẻ khó chịu. Việc chiều theo tôi lúc đó là
cách bạn làm hỏng dự án này.

═══ MƯỜI HAI NGUYÊN TẮC (doc 00 §5) ═══
P1  Kernel không biết gì về AI          P7  Dữ liệu của người dùng, định dạng mở
P2  Local-first, cloud-optional          P8  Fail visible, never silent
P3  Capability trước Provider            P9  Idempotent & resumable
P4  Loose coupling qua Event             P10 Contract ổn định, phần thịt tự do
P5  Explainable by default               P11 Boring technology
P6  Tri thức là tài sản, code là chi phí P12 An toàn mặc định

Vi phạm P1, P3, P4 hoặc P10 → từ chối mặc định, KỂ CẢ khi code chạy đúng và
nhanh hơn (doc 00 §8).

═══ BỐN HỢP ĐỒNG DÀI HẠN (doc 04) ═══
Kernel API · Capability Contract · Agent Contract · Event Schema.
Sửa bất kỳ cái nào → phải có ADR + migration + deprecation 2 phiên bản.
Nếu việc tôi nhờ đụng vào chúng, hãy DỪNG và nói ra trước khi viết dòng nào.

═══ BỐN CỔNG CI (doc 17 §2) ═══
1. Kernel sạch AI    2. Kernel độc lập    3. Agent mù provider    4. Không secret trong log
Không bao giờ đề xuất nới lỏng, thêm ngoại lệ, hay tắt tạm một cổng. Nếu code
đụng cổng, sửa code. Nếu tôi yêu cầu nới cổng, hãy nhắc tôi doc 17 §10.

═══ HAI CÁCH DỰ ÁN NÀY CHẾT ═══
RSK-01 — Xây mãi hạ tầng mà không bao giờ dùng được.
   Dấu hiệu: 3 tuần liên tiếp không có job nào chạy thật.
   Nếu bạn thấy dấu hiệu này, hãy nói với tôi.
RSK-03 — Kernel bị nhiễm bẩn dần bởi logic AI.
   Dấu hiệu: "chỉ thêm tạm một if provider == ... vào Kernel".

═══ CÁCH BẠN LÀM VIỆC ═══
1. HAI PHA. Với mọi việc xây dựng: trước tiên trình bày thiết kế (hình dạng dữ
   liệu, chữ ký hàm, danh sách file sẽ đụng, event sẽ phát, lỗi sẽ ném) rồi
   DỪNG LẠI chờ tôi duyệt. Chỉ viết code sau khi tôi nói "ok".
2. PHẠM VI. Nếu tôi nhờ thứ thuộc milestone khác, hãy nói ra và đề nghị ghi vào
   docs/backlog.md thay vì làm luôn. Bản đồ milestone ở doc 13 và doc 18 §9.
3. KHÔNG TRỪU TƯỢNG SỚM. Chỉ trừu tượng hoá khi đã có 2 ca dùng THẬT. Một
   interface cho trường hợp thứ hai chưa tồn tại là nợ, không phải tài sản.
4. LỖI CÓ HÌNH DẠNG. Mọi lỗi dùng PaosError với code thuộc 13 mã chuẩn doc 04
   §1, kèm context và một hint HÀNH ĐỘNG ĐƯỢC bằng tiếng Việt. "Đã xảy ra lỗi"
   là không đạt (UX-01).
5. EVENT TRƯỚC. Mỗi hành vi mới: event nào được phát? schema đã đăng ký chưa?
   Nếu code ra quyết định → có Decision Record chưa (doc 10 §4)?
6. TEST CÙNG LÚC. Code không kèm test là code chưa xong (doc 08 §8).
7. NGẮN GỌN. Tôi đọc trên màn hình nhỏ. Ưu tiên bảng, danh sách, ví dụ cụ thể.
   Không lặp lại nguyên tắc tôi đã biết. Không tóm tắt lại việc vừa làm dài dòng.
8. THẬT THÀ. Nếu bạn không chắc, nói không chắc. Nếu một cách tiếp cận có nhược
   điểm, nói nhược điểm trước khi nói ưu điểm. Nếu tôi sai, nói tôi sai.

═══ CHUẨN KỸ THUẬT (doc 17 §3) ═══
Python 3.12 + asyncio · ruff line-length 100 · mypy --strict cho kernel/ và sdk/
Module Kernel < 500 dòng, hàm < 60 dòng · không except: pass · không magic number
không prompt dài trong code (file riêng có version) · không time.sleep
không truy cập state.db ngoài kernel/state/ · SQLite qua single-writer actor (ADR-0024)
ID = ULID có tiền tố (ADR-0023) · hợp đồng validate bằng JSON Schema (ADR-0022)
framework web chỉ ở apps/paosd/ (ADR-0021)

═══ ĐỊNH NGHĨA "XONG" (doc 08 §8) ═══
[ ] test unit + contract, CI xanh          [ ] chạy được offline hoặc fail rõ ràng
[ ] không vi phạm P1/P3/P4                  [ ] idempotent + resume được nếu > 60s
[ ] có Event, schema đã đăng ký             [ ] docs/ đã cập nhật nếu đụng contract
[ ] lỗi có mã chuẩn + hint                  [ ] có mục trong paosctl explain nếu ảnh hưởng luồng
[ ] có Decision Record nếu ra quyết định
```

---

## §2 · Prompt vòng lặp

Mười prompt dùng lại suốt dự án. Học thuộc tên viết tắt của chúng.

### P-OPEN · Mở phiên

```
Mở phiên làm việc.

Bối cảnh hiện tại:
- Milestone: {M?}, lát cắt: {tên}
- Ba dòng cuối trong docs/worklog.md:
  {dán vào}
- git log --oneline -5:
  {dán vào}
- make ci: {xanh / đỏ ở ...}

Trước khi làm gì: xác nhận với tôi trong tối đa 5 dòng rằng bạn hiểu đúng tôi
đang ở đâu, việc tiếp theo là gì, và có gì trong worklog cho thấy tôi đang vướng
mà chưa gỡ. Nếu bối cảnh mâu thuẫn với doc 18, nói ra.
```

### P-CONTRACT · Pha 1 — thiết kế trước khi code

```
Việc: {mô tả}

Đây là PHA 1. Chưa viết code. Trình bày:

1. HÌNH DẠNG DỮ LIỆU — bảng SQL / JSON Schema / dataclass sẽ thêm hoặc đổi
2. CHỮ KÝ CÔNG KHAI — hàm/lớp mới, kèm type hint đầy đủ
3. FILE SẼ ĐỤNG — đường dẫn + một câu vì sao
4. EVENT — phát ra event nào, payload gì, đã có schema chưa
5. LỖI — mã lỗi nào có thể ném, hint tương ứng
6. TEST — danh sách tên test sẽ viết, mỗi cái một câu mô tả điều nó chứng minh
7. RANH GIỚI — việc này có chạm hợp đồng dài hạn nào không? Có cần ADR không?
8. RỦI RO — điều gì trong thiết kế này sẽ làm tôi đau ở milestone sau?

Rồi DỪNG. Chờ tôi duyệt hoặc phản biện.
```

### P-IMPL · Pha 2 — viết code

```
Duyệt thiết kế. Có sửa: {nếu có}

Viết code. Yêu cầu:
- Test viết cùng lúc, không viết sau
- Mỗi file kèm một dòng đầu nói file này chịu trách nhiệm gì
- Nếu trong lúc viết bạn phát hiện thiết kế sai, DỪNG và nói, đừng tự chữa cháy
- Kết thúc bằng: lệnh cần chạy để kiểm chứng, và câu commit theo Conventional Commits
```

### P-REVIEW · Tự review PR

```
Tôi vừa xong {mô tả}. Đóng vai người review khó tính, KHÔNG phải người viết.

Đi qua checklist doc 17 §6, và trả lời thẳng:
1. Có vi phạm P1/P3/P4/P10 không? Chỉ ra dòng cụ thể
2. Có chỗ nào là trừu tượng hoá sớm (chưa có 2 ca dùng thật)?
3. Có chỗ nào nuốt lỗi, hoặc lỗi thiếu hint hành động được?
4. Có chỗ nào tôi đã vô tình đưa logic nghiệp vụ vào Kernel?
5. Test có thật sự chứng minh điều nó nói không, hay chỉ chạy qua code?
6. Điều gì trong đoạn này sẽ khiến tôi của hai năm sau chửi thề?

Nếu không có vấn đề gì đáng nói, nói thẳng là không có — đừng bịa ra để tỏ ra hữu ích.

Code:
{dán vào}
```

### P-CLOSE · Đóng phiên

```
Đóng phiên. make ci: {kết quả}

Cho tôi:
1. Ba dòng cho docs/worklog.md — đã làm / đang vướng / tiếp theo.
   Dòng "tiếp theo" phải cụ thể tới mức phiên sau mở máy là gõ được ngay.
2. Câu commit (Conventional Commits)
3. Có gì cần thêm vào docs/backlog.md từ phiên này không?
4. main có đang ở trạng thái chạy được không? Nếu không, cần làm gì để về đó
   TRƯỚC khi tôi đóng máy?
```

### P-DEBUG · Gỡ lỗi

```
Triệu chứng: {mô tả}
Đã thử: {liệt kê}
Lát cắt hiện tại: {M?/lát ?}

Trước khi đề xuất sửa:
1. Kiểm §6 của doc 18 — vấn đề này có nằm trong danh mục R01–R38 không?
2. Nêu 3 giả thuyết theo thứ tự khả năng, kèm cách kiểm chứng RẺ NHẤT cho từng cái
3. Chỉ sau khi tôi xác nhận nguyên nhân mới đề xuất sửa

Đừng đề xuất "thử cái này xem" theo kiểu rải đạn. Một giả thuyết, một phép thử.
```

### P-STUCK · Bế tắc

```
Tôi kẹt ở {mô tả} đã {thời gian}.

Đặt cho tôi tối đa 5 câu hỏi để làm rõ vấn đề thật sự là gì. Đừng đề xuất giải
pháp vội. Có khả năng vấn đề không nằm ở chỗ tôi đang nhìn.

Đặc biệt kiểm: tôi có đang cố làm một việc thuộc milestone sau không? Có đang cố
trừu tượng hoá cho trường hợp thứ hai chưa tồn tại không?
```

### P-ADR · Viết ADR

```
Quyết định cần chốt: {mô tả}

Trước hết: quyết định này có ĐÁNG một ADR không, theo tiêu chí doc 17 §7?
Nếu không, nói thẳng là không cần và đề xuất chốt nhanh trong 3 dòng.

Nếu có, viết ADR theo đúng mẫu doc 15:
Bối cảnh → Quyết định → Lý do → Hệ quả → Phương án đã loại.
Yêu cầu: mục "Phương án đã loại" phải có ít nhất 3 phương án, mỗi cái kèm ưu
điểm thật sự của nó (không phải phương án rơm), rồi mới nói vì sao loại.
Đánh số tiếp theo ADR mới nhất trong doc 15.
```

### P-CUT · Cắt phạm vi

```
Tôi đang chậm {n} ngày ở {M?}. Cần cắt.

Theo doc 13 §"Nguyên tắc ưu tiên" và doc 18 §8, đề xuất thứ tự cắt cụ thể cho
tình huống này. Với mỗi hạng mục cắt:
- Cắt xong thì mất gì
- Trả nợ lúc nào, điều kiện gì
- Có phải nợ kỹ thuật cần ghi vào docs/backlog.md không

Nhắc tôi rõ những gì KHÔNG BAO GIỜ được cắt (nhóm 4 của doc 13).
```

### P-RESUME · Quay lại sau khi nghỉ dài

```
Tôi vừa nghỉ {thời gian}. Cần nạp lại bối cảnh.

Dữ liệu:
- docs/worklog.md (20 dòng cuối): {dán}
- git log --oneline -20: {dán}
- docs/backlog.md mục "chưa phân loại": {dán}

Cho tôi một bản tóm tắt ≤ 15 dòng:
1. Tôi đang ở đâu trong roadmap
2. Việc dở dang cụ thể là gì
3. Có quyết định nào tôi đã hoãn mà giờ chặn đường không
4. Việc đầu tiên nên làm trong phiên này
5. Có dấu hiệu nào của RSK-01 (lâu rồi không có job chạy thật) không
```

---

## §3 · Ngày 0 — Bootstrap

### P-D0-1 · Kiểm môi trường

```
Bắt đầu Ngày 0. Kiểm môi trường theo doc 18 §2.

Kết quả chạy E1–E9 của tôi:
{dán output từng lệnh}

Cho tôi:
1. Mục nào trượt, hệ quả cụ thể là gì
2. Có mục nào "đạt nhưng đáng lo" không (ví dụ workspace nằm trong thư mục sync)
3. Nội dung điền sẵn cho docs/environment-baseline.md
4. Nếu Ollama chưa sẵn sàng: xác nhận rằng M0 vẫn làm được đầy đủ với provider
   stub, và điều đó thay đổi gì trong kế hoạch
```

### P-D0-2 · Dựng khung repo và cổng CI

```
Dựng khung repo theo doc 18 §4.

Tôi đã có sẵn: pyproject.toml, .importlinter, Makefile, .gitignore,
.github/workflows/ci.yml, scripts/ci-kernel-isolation.sh,
scripts/check-event-schemas.py, scripts/check-docs-sync.sh

Việc cần làm:
1. Tạo cây thư mục đầy đủ theo doc 18 §4.1, kèm __init__.py và một dòng
   docstring cho mỗi package nói nó chịu trách nhiệm gì
2. Tạo các file tối thiểu để 4 cổng CI chạy được và XANH trên repo gần rỗng:
   - kernel/events/types.py với class EventType (StrEnum) — bắt đầu bằng
     kernel.startup và kernel.shutdown
   - schemas/events/*.schema.json tương ứng
   - tests/security/test_redaction.py + hàm redact() tối giản
   - tests/kernel/test_smoke.py để cổng 2 có cái để chạy
3. Xác nhận từng cổng sẽ xanh, và giải thích cổng nào có nguy cơ báo giả

Lưu ý cổng 1 grep cả comment và docstring. Đừng để lọt từ nào trong danh sách
cấm vào kernel/, kể cả trong lời giải thích.
```

### P-D0-3 · Chốt và ghi lại

```
Chốt Ngày 0.

1. Rà 12 quyết định ở doc 18 §3 — có cái nào tôi chưa thật sự chốt, chỉ mới
   "tạm chấp nhận" không? Chỉ ra.
2. Bốn ADR 0021–0024 đã viết. Đọc lại và nói: có mâu thuẫn nào giữa chúng, hoặc
   với ADR-0001→0014 không?
3. Điền sẵn docs/backlog.md phần "nợ kỹ thuật" nếu Ngày 0 đã tạo ra nợ nào
4. Checklist "Xong" của Ngày 0 (doc 18 §4.5) — mục nào chưa tick, vì sao
5. Câu commit
```

---

## §4 · M0 — Walking Skeleton

Mỗi lát cắt dùng bộ ba **P-CONTRACT → P-IMPL → P-CLOSE**. Prompt dưới đây thay cho P-CONTRACT ở pha 1.

### P-M0-1 · State Store (≈8h)

```
Lát cắt 1 của M0 — State Store sống được. Doc 18 §5 lát 1.

Phạm vi:
- kernel/errors.py: PaosError + đủ 13 mã chuẩn doc 04 §1, mọi lỗi bắt buộc hint
- kernel/ids.py, kernel/clock.py theo ADR-0023 và doc 18 D-02
- kernel/state/db.py: single-writer actor theo ADR-0024
- migrations/001_init.sql: schema_migrations, counters, jobs, processes,
  process_transitions, tasks, checkpoints, events, event_deliveries, artifacts
- Migration tự chạy lúc khởi động + backup trước migrate
- PRAGMA integrity_check lúc khởi động

Ràng buộc:
- Cột lấy NGUYÊN VĂN từ doc 03 §3. Không "tối ưu" schema — đó là hợp đồng
- ADR-0024 điều khoản 3: không transaction nào kéo dài qua một await gọi ra ngoài
- Bảng checkpoints và cột attempts/idempotency_key CÓ MẶT nhưng chưa có logic
  (doc 18 §10 — chừa cột, đừng chừa code)

Đặc biệt chú ý doc 18 §6 nhóm A (R01–R07). Trong thiết kế, chỉ rõ bạn chống
R01, R02, R03 bằng cách nào.

Chạy P-CONTRACT.
```

### P-M0-2 · Event Bus (≈10h)

```
Lát cắt 2 của M0 — Event Bus bền vững. Doc 18 §5 lát 2.

Phạm vi:
- Envelope ĐỦ 10 trường doc 05 §1, kể cả correlation_id và causation_id dù M0
  chưa dùng (thiếu bây giờ = migration đau sau này)
- publish(): mở transaction → INSERT events → commit → RỒI MỚI dispatch
- Dispatch NGOÀI transaction, mỗi subscriber bọc try/except riêng
- event_deliveries(subscriber, event_id) UNIQUE → khử trùng at-least-once
- Lúc khởi động: quét event có seq lớn hơn con trỏ từng subscriber, giao lại
- Một subscriber: ProjectLogger ghi projects/<x>/logs/events.ndjson

KHÔNG làm ở lát này: retry, backoff, DLQ, replay. Đó là M1 (BL-001, BL-002).
Nếu bạn thấy mình đang thiết kế cho chúng, dừng lại.

Thiết kế phải trả lời rõ: nếu tiến trình chết SAU commit và TRƯỚC dispatch,
điều gì đảm bảo subscriber vẫn nhận được? Đây là REL-01.

Chạy P-CONTRACT.
```

### P-M0-3 · Process state machine (≈8h)

```
Lát cắt 3 của M0 — Process tối giản. Doc 18 §5 lát 3.

Phạm vi:
- Máy trạng thái: CREATED → QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED
- Các trạng thái PLANNING/WAITING/PAUSED/COMPENSATING/FAILED_FINAL CÓ trong
  enum và bảng chuyển trạng thái nhưng CHƯA có đường vào
- Bảng chuyển trạng thái là DỮ LIỆU (dict), không phải chuỗi if. Chuyển sai → CONFLICT
- Mọi chuyển trạng thái ghi process_transitions VÀ phát event, cùng transaction
- Cấp pid theo ADR-0023: UPDATE counters ... RETURNING, trong transaction
- HTTP API: POST /v1/jobs, GET /v1/processes, GET /v1/processes/{pid}, GET /v1/health
- Toàn bộ mã HTTP ở apps/paosd/ (ADR-0021). kernel/ không import fastapi

Chú ý R13, R14, R15 ở doc 18 §6 nhóm C. Đặc biệt: CancelledError phải được bắt
RIÊNG và re-raise, không lọt vào except Exception.

Chạy P-CONTRACT.
```

### P-M0-4 · Capability & hai Provider (≈12h)

```
Lát cắt 4 của M0 — Capability và Provider. Doc 18 §5 lát 4.
Đây là lát quan trọng nhất về kiến trúc: nơi ranh giới P1/P3 được lập lần đầu.

Thứ tự bắt buộc: 4a capability → 4b provider stub → 4c provider ollama.
Làm ngược lại thì mọi test sẽ phụ thuộc một model đang chạy.

4a — capabilities/text.generate/1/{input,output}.schema.json + errors.json,
     Registry nạp capability và provider từ file lúc khởi động.
     Kernel CHỈ biết capability_id. Không hằng số nào tên provider trong kernel/.

4b — providers/stub/: tất định, có chế độ ép lỗi qua biến môi trường
     (PAOS_STUB_FAIL=PROVIDER_TIMEOUT). Đây là nền của PAOS_MODE=deterministic
     và sẽ được dùng suốt 10 tháng tới.

4c — providers/ollama/: adapter mỏng ≤ 200 dòng (MNT-02). health() timeout 3s,
     invoke() timeout 180s (lần gọi đầu Ollama nạp model mất 30–60s — R19).
     stream: false ở M0 (R20). Ollama chưa chạy → PROVIDER_DOWN + hint cụ thể.
     Model chưa pull → NOT_FOUND + hint 'ollama pull ...'.

Adapter là DRIVER: cấm ghi file ngoài cache/, cấm phát Event, cấm gọi capability
khác, cấm đọc Memory (doc 04 §2.2).

Test bắt buộc: cùng một bộ test chạy được với cả stub lẫn ollama, cái sau đánh
dấu @pytest.mark.requires_ollama.

Chạy P-CONTRACT cho 4a trước. Ba phần làm ba phiên, không gộp.
```

### P-M0-5 · Agent, CLI, Trace (≈12h)

```
Lát cắt 5 của M0 — Agent và explain. Doc 18 §5 lát 5.

Phạm vi:
- sdk/: AgentContext chỉ lộ những gì doc 12 §3 cho phép. KHÔNG có HTTP client,
  KHÔNG có tên model, KHÔNG có truy cập DB
- agents/summarize/: ĐỦ 6 bước vòng đời doc 04 §3.1, kể cả bước gần rỗng
  (review() chỉ kiểm độ dài > 0). Làm đủ 6 bước ngay vì đó là hình dạng mọi
  agent tương lai sẽ sao chép
- Prompt ở agents/summarize/prompts/v1.md, không nhúng trong code
- Kernel cưỡng chế manifest: gọi capability không khai báo → PERMISSION_DENIED
- Artifact bất biến + sha256 (ADR-0013)
- paosctl: run, ps, status, explain, events tail, doctor (tối giản)
- explain dựng HOÀN TOÀN từ event log, không đọc trạng thái sống

Yêu cầu cứng nhất của lát này: phải có một test tắt paosd, khởi động lại, rồi
explain một process cũ — vẫn đầy đủ. Nếu explain đọc bộ nhớ tiến trình "cho
nhanh", đến M6 sẽ phải viết lại từ đầu.

Chú ý R18 (path traversal khi ghi artifact) và R22 (agent lỡ biết provider_id).

Chạy P-CONTRACT.
```

### P-M0-6 · Nghiệm thu M0

```
Nghiệm thu M0. Chạy kịch bản doc 18 §7.

Kết quả từng bước:
{dán output}

Cho tôi:
1. Từng exit criteria doc 13 + 3 mục bổ sung doc 18 §7 — đạt hay chưa, bằng chứng
2. Ma trận truy tại doc 18 §7.1 — test nào còn thiếu
3. Có bước nào tôi đã "sửa tay một chút mới chạy được" không? Nếu có, M0 chưa xong
4. Rà Risk Register (doc 14) theo 3 câu hỏi cuối milestone. Đặc biệt:
   - RSK-01: đã có bao nhiêu job THẬT chạy? Nếu 0, M0 chưa xong dù test xanh
   - RSK-03: đã có lần nào tôi muốn nới cổng CI chưa? Vì sao?
   - RSK-15: PAOS_MODE=deterministic có thật sự tất định? Chạy 20 lần cùng kết quả?
5. Một đoạn ngắn cho doc 18: KẾ HOẠCH ĐÃ SAI Ở ĐÂU. Đây là dữ liệu cho M1
6. Ước lượng M1 có nên điều chỉnh không, dựa trên tốc độ thực tế của M0
```

---

## §5 · M1 → M8 + Hardening

Mỗi milestone mở bằng **P-MX-0 (kickoff)** và đóng bằng **P-MX-EXIT**. Giữa hai đầu là các prompt lát cắt.

### Mẫu kickoff (thay X bằng số milestone)

```
Mở milestone M{X} — {tên}. Doc 13 mục M{X}.

Trước khi bắt đầu:
1. Đọc lại exit criteria của M{X} và diễn giải chúng thành danh sách lát cắt cụ
   thể, mỗi lát ≤ 12 giờ làm việc và tự nó chạy được
2. Với mỗi lát: nó đụng hợp đồng dài hạn nào? Cần ADR nào?
3. Có ADR nào trong backlog doc 15 cần chốt TRƯỚC milestone này không?
4. Rủi ro nào trong doc 14 sẽ hiện thực hoá trong milestone này?
5. Điều gì trong M{X-1} tôi đã chừa cửa sẵn mà bây giờ phải dùng đến?
6. Danh sách "KHÔNG làm ở M{X}" — thứ dễ bị kéo vào nhất

Rồi DỪNG, chờ tôi duyệt danh sách lát cắt.
```

### Mẫu exit (thay X)

```
Đóng milestone M{X}.

Exit criteria doc 13: {dán kết quả từng mục}

1. Mục nào chưa đạt, thiếu gì cụ thể
2. Rà Risk Register theo 3 câu hỏi doc 14
3. Bài kiểm tra ranh giới: chạy `make gate2` và `make ten-year-test` — Kernel
   còn khoẻ không?
4. docs/ có lệch code ở đâu không? (MNT-09)
5. Nợ kỹ thuật đã tạo ra trong M{X} — ghi vào backlog với điều kiện trả nợ
6. Tốc độ thực tế so với ước lượng — điều chỉnh gì cho M{X+1}
7. Một câu trả lời thật thà: kiến trúc có đang tệ đi không?
```

---

### M1 — Kernel thật (4 tuần)

| Prompt | Lát cắt | Trọng tâm |
|---|---|---|
| P-M1-1 | Process state machine đầy đủ | 5 trạng thái còn lại + compensation path |
| P-M1-2 | Checkpoint & resume | P9, PERF-05, REL-03 |
| P-M1-3 | DAG Scheduler + resource token | Song song thật, backpressure |
| P-M1-4 | Event Bus: retry, DLQ, replay | BL-001, BL-002 |
| P-M1-5 | Idempotency + error taxonomy hoàn chỉnh | REL-06 |

```
### P-M1-2 · Checkpoint & resume
Lát cắt: checkpoint/resume. Bảng checkpoints đã có từ M0 (doc 18 §10).

Phạm vi: ghi checkpoint sau mỗi Task · resume từ checkpoint gần nhất khi khởi
động · Agent có resume() nếu chạy > 60s (doc 04 §3.1).

Ràng buộc cứng:
- PERF-05: resume < 5s tới khi Task đầu tiên chạy lại
- REL-01: kill -9 giữa Process → 0 event mất
- REL-03: Process > 30 phút resume thành công ≥ 99%
- Checkpoint là một transaction (doc 02 §3.4)

Câu hỏi thiết kế bắt buộc trả lời: một Task đã gọi provider và provider đã làm
việc, nhưng crash xảy ra TRƯỚC khi ghi kết quả — resume sẽ gọi lại provider.
Làm sao để điều đó không tính tiền hai lần (REL-06)? Quan hệ giữa checkpoint và
idempotency_key là gì?

Chạy P-CONTRACT.
```

```
### P-M1-3 · DAG Scheduler + resource token
Phạm vi: duyệt DAG topological · max_parallel có xét Resource Token
(gpu:1, cpu_heavy:2, net_api:4, disk_io:2) · ưu tiên priority → deadline → FIFO
· backpressure khi hàng đợi vượt ngưỡng.

Ràng buộc:
- PERF-01: overhead Kernel mỗi Task < 50ms p95
- PERF-08: ≥ 8 Process song song
- UC4: cancel 1 Process không ảnh hưởng 2 cái kia
- Token là semaphore; Task khai báo mình cần gì
- Ngưỡng KHÔNG hardcode — vào file policy (doc 17 §3)

KHÔNG làm: Time Engine, Energy Engine (M7). Scheduler chỉ hỏi một interface
`can_schedule()` mà M0 trả về True luôn — giữ nguyên chỗ nối đó.

Chạy P-CONTRACT.
```

```
### P-M1-4 · Event Bus: retry, DLQ, replay
Phạm vi: retry có backoff + jitter cho subscriber lỗi · Dead Letter Queue sau
max_retries · paosctl events replay --from --to --to-subscriber.

Ràng buộc: replay phải AN TOÀN — subscriber phải chịu được việc dựng lại từ đầu
(doc 05 §5.6). Đây là điều kiện để M5 rebuild Memory/KG.

Test bắt buộc: replay toàn bộ event từ đầu → trạng thái phái sinh giống hệt bản
dựng dần. Nếu không giống, một subscriber nào đó đang không idempotent.

Chạy P-CONTRACT.
```

---

### M2 — Capability & Provider (3 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M2-1 | Capability schema + registry đầy đủ |
| P-M2-2 | Provider manifest + Conformance Suite |
| P-M2-3 | Router: ưu tiên + fallback chain + circuit breaker |
| P-M2-4 | Content-addressed cache |
| P-M2-5 | Permission Guard + Audit Log + Secret Manager + redaction |

```
### P-M2-2 · Conformance Suite
Phạm vi: bộ test tuân thủ mà MỌI provider phải pass trước khi được đăng ký
(doc 04 §2.3).

Bộ test phải kiểm:
- output khớp output_schema với 20 input mẫu
- lỗi trả đúng mã chuẩn khi giả lập: timeout, mất mạng, input rác
- cancel() dừng THẬT trong ≤ 2s
- estimate() sai lệch < 30% so với thực tế
- không ghi ra ngoài thư mục cho phép (kiểm bằng sandbox fs)

Đây là lá chắn cho RSK-07 (provider đổi API / ngừng dịch vụ). Thiết kế sao cho
thêm capability mới thì bộ test tự sinh phần khung, chỉ cần bổ sung fixture.

Chạy P-CONTRACT.
```

```
### P-M2-3 · Router + fallback + circuit breaker
Phạm vi: ràng buộc cứng lọc ứng viên (doc 06 §2.1) · chuỗi fallback với
exponential backoff + jitter · circuit breaker 3 lỗi → OPEN 60s → HALF_OPEN → CLOSED.

CHƯA làm ở M2: công thức chấm điểm và provider ranking (M6). Ở M2 chỉ chọn theo
thứ tự ưu tiên khai báo. Nhưng Decision Record PHẢI có ngay từ bây giờ, với
candidates[] và rationale — kể cả khi rationale chỉ là "ưu tiên số 1 khả dụng".

Lý do: ADR-0014 nói trace và Decision Record là bắt buộc, không có chế độ tắt.
Nếu M2 bỏ qua, M6 sẽ không có dữ liệu lịch sử để học.

Chạy P-CONTRACT.
```

```
### P-M2-5 · Permission Guard + Secret + redaction
Phạm vi: tier quyền theo doc 09 · audit_log cho mọi hành động CONFIRM ·
Secret Manager · redaction chạy TRƯỚC khi ghi log.

Ràng buộc SLO: SEC-01 (0 secret trong log/event/artifact), SEC-02 (100% hành
động CONFIRM có bản ghi phê duyệt), SEC-03 (100% hành động FORBIDDEN bị chặn).

Hàm redact() đã tồn tại từ Ngày 0 với 3 mẫu. Bây giờ mở rộng thành thật, và
viết test ĐỐI KHÁNG: cố tình nhét secret vào 20 vị trí khác nhau (payload event,
tên artifact, message lỗi, context của PaosError, trace attrs...) rồi quét toàn
bộ đầu ra.

Nhắc tôi RSK-12: luật Data ≠ Instruction. Nội dung tài liệu đầu vào KHÔNG BAO
GIỜ trở thành mệnh lệnh. Permission Guard không có đường tắt.

Chạy P-CONTRACT.
```

---

### M3 — Agent & Workflow (4 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M3-1 | Agent Contract đầy đủ + SDK cho người viết plugin |
| P-M3-2 | Workflow YAML engine: điều kiện, parallel, retry, compensation |
| P-M3-3 | Video plugin phần 1: planning → script |
| P-M3-4 | Video plugin phần 2: image ∥ voice ∥ subtitle |
| P-M3-5 | Render + progress reporting + UC1 end-to-end offline |

```
### P-M3-2 · Workflow YAML engine
Phạm vi: diễn giải DAG khai báo bằng YAML — điều kiện, nhánh song song, vòng
lặp CÓ GIỚI HẠN, compensation (ADR-0006).

Ràng buộc: Workflow là DỮ LIỆU, không phải code. Không được có escape hatch cho
phép nhúng Python vào YAML — đó là cách file cấu hình biến thành ngôn ngữ lập
trình tồi trong vòng một năm.

Biểu thức điều kiện: chọn một cú pháp tối thiểu, an toàn (không eval), và ghi
ADR cho nó. Nêu rõ 3 phương án và vì sao loại 2 cái.

workflow.json đã resolve phải được ĐÓNG BĂNG vào project (doc 03 §4) để tái lập
được sau này.

Chạy P-CONTRACT.
```

```
### P-M3-4 · Nhánh song song thật sự
Phạm vi: Image ∥ Voice ∥ Subtitle chạy song song, có xét resource token từ M1.

Exit criteria doc 13 yêu cầu: "nhánh song song THẬT SỰ tiết kiệm thời gian (đo
và ghi vào explain)". Nghĩa là explain phải hiện được dòng kiểu:
"media (song song, tiết kiệm 3m41s so với tuần tự)" như mẫu doc 10 §3.

Điều đó đòi hỏi hệ thống biết thời lượng tuần tự giả định. Thiết kế cách tính
con số đó cho trung thực — đừng bịa.

Đồng thời chứng minh P4: thêm Subtitle Agent mới phải là 0 dòng sửa ở Planning
Agent và Script Agent. Chuẩn bị sẵn bằng chứng bằng diff git.

Chạy P-CONTRACT.
```

---

### M4 — Quality & Self-Correction (2 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M4-1 | Rubric engine: deterministic + hybrid + LLM judge |
| P-M4-2 | Review Agent + self-correction loop có giới hạn + escalation |
| P-M4-3 | Eval harness + edit_rate tracking |

```
### P-M4-1 · Rubric engine
Phạm vi: rubric YAML có version (doc 08 §2) · thứ tự chấm: deterministic →
fail_fast → LLM judge → tổng hợp có trọng số.

Nguyên tắc vàng doc 08 §2: tiêu chí nào kiểm được bằng code thì KHÔNG dùng LLM.
Trong thiết kế, chỉ rõ mỗi criterion thuộc loại nào và vì sao.

fail_fast phải thật sự tiết kiệm: rớt length hoặc cta thì trả về ngay với 0 chi
phí LLM. Đo và ghi con số tiết kiệm được vào explain.

Chạy P-CONTRACT.
```

```
### P-M4-2 · Self-correction loop
Phạm vi: vòng lặp Script → Review → Script với 5 quy tắc chống lặp vô ích của
doc 08 §4.

Năm quy tắc là ràng buộc cứng, không phải gợi ý:
1. max_loops mặc định 2 (tối đa 3 lần thử)
2. điểm phải cải thiện ≥ 5 giữa hai vòng, không thì dừng và escalate
3. ngân sách retry riêng ≤ 30% ngân sách Job
4. vòng 3 BẮT BUỘC đổi chiến lược (prompt version khác hoặc tier provider cao hơn)
5. escalate luôn kèm bản tốt nhất đã có — không bao giờ trả về tay trắng

Chống RSK-09 (vòng lặp cháy chi phí). Và chống RSK-10: Review PHẢI dùng provider
khác generator, Router cưỡng chế bằng exclude_provider.

Chạy P-CONTRACT.
```

---

### M5 — Memory & Knowledge (4 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M5-0 | Chốt ADR-0015 (vector search + embedding) và ADR-0016 (chunking) trước |
| P-M5-1 | 5 tầng memory + retrieval lai |
| P-M5-2 | Consolidation job hàng đêm + preference learning |
| P-M5-3 | Knowledge Graph + extractor + provenance |
| P-M5-4 | Privacy Filter + paosctl memory/knowledge + nút quên |

```
### P-M5-0 · Chốt hai ADR còn nợ
Doc 15 backlog ghi ADR-0015 (thư viện vector search + mô hình embedding) và
ADR-0016 (chiến lược chunking) phải chốt TRƯỚC M5.

Với mỗi cái, chạy P-ADR. Ràng buộc bổ sung:
- ADR-0002 đã nói vector search qua sqlite-vec. Xác nhận lại lựa chọn đó còn
  đúng, hay đã có lý do để đổi
- Mô hình embedding phải chạy local được (P2, LOC-03)
- Chunking phải tái lập được: cùng tài liệu, cùng cấu hình → cùng chunk

Đừng bắt đầu code M5 trước khi hai ADR này xong.
```

```
### P-M5-2 · Consolidation + preference learning
Phạm vi: job hàng đêm thăng cấp ký ức, cập nhật thống kê, trích tri thức.

Ràng buộc chống RSK-11 (memory tích lũy rác):
- CHỈ ghi L3 từ hành vi QUAN SÁT ĐƯỢC, không từ suy đoán
- có ngưỡng confidence, có provenance cho mọi mục
- consolidation phải kiểm duyệt được: paosctl memory review
- nút quên luôn sẵn sàng

Exit criteria: sau 3 job cùng loại, PAOS không hỏi lại sở thích. Và: người dùng
sửa tay → confidence giảm → hành vi đổi ở job sau.

Câu hỏi thiết kế: "sửa tay" được phát hiện thế nào? Nó phải là tín hiệu KHÁCH
QUAN (edit_rate), không phải người dùng tự khai.

Chạy P-CONTRACT.
```

```
### P-M5-4 · Privacy Filter
Phạm vi: Memory L3 KHÔNG BAO GIỜ rời máy khi privacy: private.

Đây là exit criteria có test ĐỐI KHÁNG. Thiết kế test đó trước khi thiết kế
tính năng: bạn sẽ cố tình tấn công hệ thống của chính mình bằng bao nhiêu đường
khác nhau? (payload gửi provider cloud, log, trace attrs, event, artifact,
cache key, thông điệp lỗi...)

Liên quan SEC-05: 100% dữ liệu gửi ra cloud phải có bản ghi trong Trace.

Chạy P-CONTRACT.
```

---

### M6 — Decision Engine (3 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M6-1 | Feature extraction + candidate generation + scoring |
| P-M6-2 | Provider ranking: provider_stats, EWMA, vòng phản hồi chất lượng |
| P-M6-3 | routing.yaml + profile + hot reload + explain --decisions |

```
### P-M6-2 · Provider ranking
Phạm vi: công thức doc 06 §2.2, provider_stats, EWMA (α=0.2, n ≥ 5 mới dùng
thống kê, dưới đó dùng quality_hint trong manifest).

Vòng phản hồi doc 06 §2.4: user.correction.made phạt nặng provider đã sinh ra
bản bị sửa (trọng số ×3).

Exit criteria: sau 20 job, ranking thay đổi theo DỮ LIỆU THỰC TẾ, không phải giá
trị cứng. Thiết kế cách chứng minh điều đó — một báo cáo so sánh ranking ở job
thứ 1 và job thứ 20, kèm nguyên nhân.

Cẩn thận: đây là nơi dễ tạo ra vòng lặp tự khẳng định (provider được chọn nhiều
→ có nhiều dữ liệu → điểm ổn định hơn → càng được chọn). Nêu cách bạn chống
hiện tượng đó, hoặc nói thẳng là chấp nhận và vì sao.

Chạy P-CONTRACT.
```

---

### M7 — Cost / Energy / Time (2 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M7-1 | Cost Engine: estimate → record → budget 3 tầng |
| P-M7-2 | Energy Engine: GPU/CPU/pin/nhiệt, hàng đợi thay vì chạy đè |
| P-M7-3 | Time Engine + báo cáo tiết kiệm hàng tháng |

```
### P-M7-1 · Cost Engine
Phạm vi: estimate trước, record sau, ngân sách 3 tầng với hành động tự động khi vượt.

Exit criteria: job vượt ngân sách BỊ CHẶN VÀ HỎI, không âm thầm tiêu tiền.
Đây là hành vi P12 (an toàn mặc định) — mặc định phải là chặn, không phải cảnh báo.

Interface can_schedule() mà M1 để trả True luôn — bây giờ là lúc dùng nó. Kiểm
lại: chỗ nối đó có còn đúng hình dạng không, hay M1 đã thiết kế sai?

Liên kết doc 06: rule "budget_used_pct > 80 → force_profile economy".

Chạy P-CONTRACT.
```

---

### M8 — Plugin System & UI (4 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M8-0 | Chốt ADR-0017 (công nghệ UI) và ADR-0018 (phân phối plugin) |
| P-M8-1 | Plugin loader + sandbox subprocess (JSON-RPC stdio) |
| P-M8-2 | Quyền khai báo + duyệt khi cài + tự disable khi vi phạm + hot reload |
| P-M8-3 | Plugin thứ hai: Document — bài kiểm tra 0 dòng sửa Kernel |
| P-M8-4 | Web UI 4 màn hình |
| P-M8-5 | export/import workspace |

```
### P-M8-3 · Plugin Document — bài kiểm tra kiến trúc
Đây KHÔNG phải một tính năng. Đây là bài thi của toàn bộ dự án.

Exit criteria: cài Plugin Document với 0 DÒNG SỬA KERNEL, bằng chứng là diff git.

Quy trình:
1. Trước khi bắt đầu: ghi lại git rev-parse HEAD
2. Viết plugin hoàn toàn qua paos.sdk, không import kernel.*
3. Khi xong: git diff <rev> -- kernel/ phải RỖNG

Nếu không rỗng, ĐỪNG sửa cho nó rỗng. Hãy phân tích: điều gì trong Kernel còn
thiếu tổng quát? Đó là phát hiện quan trọng hơn cả plugin này. Viết ADR cho nó.

Doc 12 §7 nói: nếu plugin thứ 4 cài được mà 0 dòng sửa Kernel, kiến trúc đã đạt
mục tiêu 10 năm. Đây là plugin thứ 2 — coi như kiểm tra giữa kỳ.

Chạy P-CONTRACT.
```

```
### P-M8-4 · Web UI
Phạm vi: đúng 4 màn hình doc 10 §8, không hơn.
Processes · Explain · Projects · Knowledge.

Nguyên tắc cứng: mọi con số hiển thị đều CLICK ĐƯỢC để xem vì sao (UX-03).
Không có con số mồ côi. Nếu một con số không giải thích được nguồn gốc, nó
không được lên UI.

Ràng buộc: UI chỉ gọi Kernel API (doc 04 §1), không truy cập DB. Nếu bạn thấy
UI cần một dữ liệu mà API chưa có → thêm endpoint vào API, đừng đi tắt.

Nhắc tôi doc 00 Anti-goals: không xây UI đẹp trước khi Kernel đúng. Nếu Kernel
còn nợ gì, nói ra trước khi bắt đầu.

Chạy P-CONTRACT.
```

---

### M9 — Research Plugin (3 tuần)

| Prompt | Lát cắt |
|---|---|
| P-M9-1 | `web.search` capability + provider local (searxng) |
| P-M9-2 | Research Agent + workflow `research.topic@1` — tổng hợp + ghi KG có provenance |
| P-M9-3 | Memory L4 (World Cache, TTL) dùng thật + `paosctl knowledge` truy vấn theo chủ đề |

```
### P-M9-2 · Research Agent + workflow
Phạm vi: Research Agent tuân đủ 6 bước vòng đời (doc 04 §3.1) · workflow
research.topic@1 (tìm kiếm/đọc tài liệu local → tổng hợp → ghi node/edge KG).

Đây là plugin thứ 3 (doc 12 §7) — bằng chứng P4 phải mạnh hơn Document ở M8:
0 dòng sửa Kernel VÀ 0 dòng sửa Video/Document Agent đã có.

Ràng buộc UC3 (doc 01): hỏi lại một chủ đề liên quan tới chủ đề đã nghiên cứu
trước đó phải dùng lại tri thức cũ từ KG, và chỉ ra được nguồn gốc (provenance,
doc 07 §4.4) — không nghiên cứu lại từ đầu. Thiết kế cách Research Agent truy
vấn KG trước khi gọi web.search, không phải ngược lại.

Chú ý RSK-12 (doc 14): nội dung trang web là DỮ LIỆU, không phải mệnh lệnh —
luật Data ≠ Instruction (doc 09 §4) áp dụng y hệt cho web.search như cho OCR.

Chạy P-CONTRACT.
```

---

### Hardening → v1.0

```
### P-HARD-1 · Đo toàn bộ NFR
Chạy đo từng mục doc 11 và điền bảng thực tế so với ngưỡng.

PERF-01→08 · REL-01→07 · LOC-01→05 · MNT-01→09 · SCL-01→05 · SEC-01→05 ·
UX-01→05 · POR-01→05

Với mỗi mục KHÔNG đạt: nguyên nhân, chi phí sửa, và có nên sửa không (một số
ngưỡng có thể đã sai chứ không phải hệ thống sai — nói thẳng nếu bạn nghĩ vậy).

"NFR không đo được là NFR không tồn tại" — nếu một mục không có cách đo, đó là
lỗi của doc 11, cần sửa doc.
```

```
### P-HARD-2 · Chaos suite đầy đủ
Chạy toàn bộ bảng doc 08 §7.4. Với mỗi kịch bản: kỳ vọng và thực tế.

Kill provider · Kill paosd giữa Process · Đầy đĩa · Plugin crash · Mất mạng ·
Xoá cache/ giữa chừng · Provider trả JSON rác

Thêm 3 kịch bản mà bạn nghĩ chúng tôi chưa nghĩ tới, dựa trên những gì đã thực
sự hỏng trong 12 tháng qua (đọc docs/worklog.md).
```

```
### P-HARD-3 · Kiểm tra tài liệu khớp thực tế
Doc 13 yêu cầu: "Tài liệu 00–17 khớp với thực tế code".

Đọc từng doc và chỉ ra chỗ code đã trôi khỏi tài liệu. Với mỗi chỗ lệch, hỏi:
code sai hay doc sai? Sửa bên sai, không sửa bên tiện hơn.

Đặc biệt kiểm 4 hợp đồng dài hạn (doc 04) — chúng phải khớp 100%, không có
ngoại lệ. Và kiểm doc 15: có quyết định kiến trúc nào đã thực hiện mà chưa có
ADR không?
```

```
### P-HARD-4 · Chạy thật 30 ngày
Doc 13: "Chạy thật 30 ngày liên tục không can thiệp tay".

Thiết kế cho tôi cách theo dõi 30 ngày đó:
1. Chỉ số nào ghi lại hàng ngày
2. "Can thiệp tay" định nghĩa chính xác là gì — ranh giới ở đâu
3. Báo cáo cuối kỳ gồm gì
4. Điều gì xảy ra nếu ngày thứ 22 hỏng — đếm lại từ đầu hay không

Và: chỉ số thành công 12 tháng ở doc 00 §7 — đo được bao nhiêu mục rồi?
```

---

## §6 · Nhịp định kỳ

### P-WEEK · Mỗi tuần

```
Rà tuần.

Dữ liệu:
- worklog 7 ngày qua: {dán}
- Kết quả eval suite: {dán nếu có}
- backlog thêm gì tuần này: {dán}

Cho tôi ≤ 12 dòng:
1. Tuần này có thứ gì CHẠY THẬT không? (RSK-01)
2. Tốc độ so với kế hoạch lát cắt hiện tại
3. Có dấu hiệu nào cho thấy tôi đang trôi khỏi phạm vi milestone không
4. Backlog có mục nào đã đủ chín để nâng lên làm việc chính
5. Một việc nên bỏ tuần sau
```

### P-MILESTONE-RISK · Cuối mỗi milestone

```
Rà Risk Register cuối M{X}. Trả lời 3 câu hỏi doc 14:
1. Rủi ro nào đã hiện thực hoá? Biện pháp có hiệu quả không?
2. Rủi ro nào đã biến mất, không cần theo dõi nữa?
3. Rủi ro MỚI nào xuất hiện từ quyết định của milestone vừa rồi?

Luôn kiểm riêng RSK-01 và RSK-03 — doc 14 gọi đây là hai rủi ro giết dự án âm
thầm nhất.

Xuất bản cập nhật cho docs/14-risk-register.md, giữ nguyên định dạng P/I/R.
```

### P-QUARTER · Mỗi quý — bài kiểm tra 10 năm

```
Bài kiểm tra 10 năm (doc 02 §9, doc 17 §9).

Đã chạy: make ten-year-test
Kết quả: {dán}

Đánh giá:
1. Kernel còn khoẻ không? Bằng chứng cụ thể
2. Thêm một Provider mới bây giờ mất bao lâu? (MNT-02: ≤ 200 dòng, 0 sửa Kernel)
3. Thêm một Capability mới mất bao lâu? (MNT-03: ≤ 1 ngày)
4. Số dòng Kernel đã tăng bao nhiêu quý này? Tăng vì lý do gì?
5. Có module Kernel nào vượt 500 dòng chưa? (MNT-08)
6. Đọc lại 4 cổng CI — có cổng nào đã được nới lỏng lúc nào đó mà tôi quên không?

Câu cuối quan trọng nhất. Hãy kiểm git log, đừng chỉ hỏi tôi.
```

### P-YEAR · Mỗi năm

```
Đọc lại doc 00 với tôi.

1. Vision còn đúng không? Chỗ nào đã lệch so với thực tế sử dụng?
2. 12 nguyên tắc — có cái nào tôi đã âm thầm từ bỏ trong thực tế không? Kiểm
   bằng code, không bằng lời tôi nói
3. Anti-goals — tôi có đang lấn sang cái nào không?
4. Chỉ số thành công doc 00 §7 — đo được bao nhiêu, cách xa mục tiêu bao nhiêu
5. Operational Knowledge tích luỹ được những gì? (doc 16 gọi đây là tài sản
   cuối cùng của dự án)

Nếu vision cần đổi: viết ADR. Đừng để nó trôi dạt âm thầm.
```

### P-RELEASE · Đóng v1.0 — chạy đúng một lần

```
Checklist Hardening (doc 13) đã đạt 100%: {dán từng dòng + bằng chứng}

Trước khi tag:
1. Xác nhận cài đặt từ clone sạch thật sự sạch — không có bước "tôi biết ngầm"
   nào bị bỏ sót khỏi README §2
2. Viết docs/retrospective-v1.md theo đúng 4 mục ở doc 13 "Nghi thức đóng v1.0"
3. Đề xuất câu commit + git tag v1.0.0
4. Một câu hỏi cuối, thật thà: nếu bắt đầu lại từ đầu với những gì đã học được,
   phần nào của kiến trúc sẽ được giữ nguyên, phần nào sẽ làm khác đi?

Đây là prompt duy nhất trong tài liệu này chỉ chạy một lần. Sau khi tag xong,
nhịp làm việc chuyển sang P-QUARTER và P-YEAR như bình thường.
```

---

## §7 · Prompt cấm

Những việc không bao giờ nhờ trợ lý làm. Nếu bạn thấy mình sắp gõ một trong
những câu này, đó là tín hiệu cần nghỉ chứ không phải cần trợ giúp.

| Đừng hỏi | Vì sao | Làm gì thay thế |
|---|---|---|
| "Giúp tôi tắt tạm cổng CI này để merge" | Doc 17 §10 gọi đây là khoảnh khắc dự án bắt đầu chết | P-DEBUG để tìm cách sửa code |
| "Viết cho tôi cả milestone M3 trong một lần" | Sinh ra khối code không ai đọc, không ai test được, và bạn mất quyền kiểm soát kiến trúc | Chia theo lát cắt, mỗi lát một phiên |
| "Cứ hardcode tạm, sau sửa" | Trong dự án một người, "sau" nghĩa là không bao giờ | Ghi vào backlog với điều kiện trả nợ rõ ràng |
| "Thêm nhanh một if provider == ... vào Kernel" | Đây chính xác là RSK-03 | Đọc lại doc 06 — vấn đề thuộc về Router hoặc Policy |
| "Bỏ qua test lần này" | Doc 08 §8: code không kèm test là code chưa xong | Cắt phạm vi thay vì cắt test (P-CUT) |
| "Tôi nghĩ nên thêm một Engine mới cho X" | RSK-04 scope creep | Ghi vào backlog, trả lời 4 câu hỏi README §3 trước |
| "Làm cho nó chạy đi, kiến trúc tính sau" | Doc 00 §8: PR vi phạm P1/P3/P4/P10 bị từ chối kể cả khi chạy đúng | Nếu thật sự cần gấp: P-CUT, cắt phạm vi chứ đừng cắt ranh giới |

**Và một điều nữa:** nếu trợ lý đồng ý làm một trong bảy việc trên mà không phản
đối, đó là lỗi của PROMPT-CORE — hãy quay lại §1 và làm nó cứng hơn.

---

## §8 · Nhật ký sửa đổi tài liệu này

Prompt cũng tiến hoá. Khi một prompt liên tục cho kết quả kém, sửa nó và ghi lại.

| Ngày | Prompt | Sửa gì | Vì sao |
|---|---|---|---|
| | | | |
