# 15 — Architecture Decision Records (ADR)

**Mẫu:** mỗi ADR gồm Bối cảnh → Quyết định → Lý do → Hệ quả → Phương án đã loại.
**Quy tắc:** ADR không bao giờ bị xóa hay sửa nội dung. Muốn đổi ý → viết ADR mới có trạng thái `Supersedes ADR-XXXX`.

---

## ADR-0001 — Python 3.12 + asyncio cho Kernel
**Trạng thái:** Accepted · 2026-08

**Bối cảnh:** Kernel điều phối I/O nhiều (gọi provider, đọc file, chờ tiến trình), tính toán nặng nằm ở provider.
**Quyết định:** Kernel viết bằng Python 3.12, mô hình bất đồng bộ asyncio, một tiến trình `paosd`.
**Lý do:** hệ sinh thái AI/tooling phong phú nhất; tốc độ phát triển cao — yếu tố sống còn với một người làm; công việc là I/O-bound nên GIL không phải nút thắt; dễ đọc lại sau 2 năm.
**Hệ quả:** phải kỷ luật về typing (`mypy --strict` cho `kernel/`); công việc CPU nặng phải đẩy sang subprocess; nếu một module nghẽn thật sự, contract cho phép viết lại bằng Rust mà không ảnh hưởng phần còn lại.
**Đã loại:** Go (hệ sinh thái AI yếu hơn, chậm phát triển hơn) · Rust (an toàn nhất nhưng tốc độ phát triển không phù hợp với 1 người) · TypeScript (tốt cho UI, yếu cho tooling AI local).

---

## ADR-0002 — SQLite (WAL) cho trạng thái, filesystem cho artifact
**Trạng thái:** Accepted

**Quyết định:** một file `state.db` (SQLite, WAL) cho state/event/registry/memory/KG; artifact là file thật trên đĩa, DB chỉ giữ metadata + hash.
**Lý do:** 0 cấu hình, 0 dịch vụ nền, transaction ACID thật, định dạng sống hơn 20 năm và có công cụ đọc ở khắp nơi; artifact là file thật giúp bạn mở bằng tay, backup bằng tay, không lock-in (P7).
**Hệ quả:** một writer duy nhất (Kernel); truy vấn phân tích nặng cần rollup; vector search qua `sqlite-vec`.
**Đã loại:** PostgreSQL (thừa với 1 người, thêm dịch vụ nền) · lưu artifact dạng BLOB trong DB (mất tính mở, DB phình) · DuckDB (mạnh về phân tích, yếu về ghi giao dịch liên tục).

---

## ADR-0003 — Giao tiếp qua Event Bus bền vững, không gọi trực tiếp
**Trạng thái:** Accepted

**Quyết định:** thành phần không gọi trực tiếp nhau; giao tiếp qua Event ghi vào SQLite **trước** khi dispatch; at-least-once; subscriber phải idempotent.
**Lý do:** đây là cơ chế thực thi P4 (Loose Coupling) — thêm Agent mới mà không sửa Agent cũ; đồng thời cho phép replay để dựng lại Memory/KG, và cho phép mọi thứ khác (Trace, Cost, Stats) dựng từ một nguồn sự thật duy nhất.
**Hệ quả:** phức tạp hơn gọi hàm; phải xử lý trùng lặp và thứ tự; cần DLQ.
**Đã loại:** gọi hàm trực tiếp (gắn kết chặt, chết ở năm thứ 2) · message broker ngoài như Redis/NATS (thêm dịch vụ nền, vi phạm nguyên tắc đơn giản vận hành).

---

## ADR-0004 — Mọi truy cập AI phải đi qua Capability
**Trạng thái:** Accepted · **Không thể thương lượng**

**Quyết định:** Agent chỉ khai báo nhu cầu (`text.generate@1`), không bao giờ biết provider nào phục vụ. Kernel cưỡng chế danh sách capability khai báo trong manifest.
**Lý do:** đây là điều kiện cần để đạt mục tiêu 10 năm — thay model chỉ là thay động cơ. Đồng thời cho phép fallback, ranking, caching, cost control tập trung ở một chỗ thay vì rải rác.
**Hệ quả:** thêm một lớp gián tiếp; capability phải thiết kế đủ tổng quát nhưng không được tổng quát tới mức vô nghĩa (bài học: nếu một capability chỉ có đúng 1 provider khả dĩ mãi mãi, có thể nó đang sai mức trừu tượng).
**Đã loại:** gọi thẳng SDK vendor (nhanh hơn hôm nay, chết vào lúc đổi vendor) · lớp adapter mỏng kiểu LangChain (vẫn để lộ khái niệm vendor lên Agent).

---

## ADR-0005 — Plugin chạy ở tiến trình riêng, giao tiếp JSON-RPC qua stdio
**Trạng thái:** Accepted

**Quyết định:** plugin mặc định là subprocess, giao thức JSON-RPC qua stdio, quyền khai báo trong manifest.
**Lý do:** cô lập lỗi (plugin crash không giết Kernel), cưỡng chế được quyền, cho phép plugin viết bằng ngôn ngữ khác, và tương thích khái niệm với các chuẩn tool-server hiện hành.
**Hệ quả:** overhead IPC (chấp nhận được vì task đơn vị tính bằng giây); cần giám sát tiến trình, timeout, restart.
**Đã loại:** in-process import (nhanh nhất nhưng plugin xấu giết cả hệ thống) · container (nặng, vi phạm "cài đặt 1 lệnh") · WASM (hấp dẫn nhưng hệ sinh thái chưa đủ cho tác vụ AI local năm 2026 — xem lại ở v2).

---

## ADR-0006 — Workflow khai báo bằng YAML, không phải code
**Trạng thái:** Accepted

**Quyết định:** workflow là file YAML với DAG, điều kiện, parallel, retry, loop có giới hạn; biểu thức `${...}` chỉ đọc dữ liệu, không eval code tùy ý.
**Lý do:** workflow là **dữ liệu**, nên nó có thể được Decision Engine chọn tự động, được đóng băng vào Project để tái lập, được so sánh giữa các phiên bản, và được người dùng đọc/sửa mà không cần lập trình.
**Hệ quả:** engine phải hỗ trợ đủ cấu trúc điều khiển mà không biến YAML thành ngôn ngữ lập trình tồi; luật cứng: nếu cần logic phức tạp hơn → viết một Agent, đừng làm phức tạp YAML.
**Đã loại:** workflow bằng Python (mạnh nhưng không phải dữ liệu, không tự chọn được, khó đóng băng) · BPMN/DSL riêng (quá nặng cho quy mô này).

---

## ADR-0007 — Local-first, cloud là tùy chọn
**Trạng thái:** Accepted

**Quyết định:** mọi capability lõi phải có ít nhất một provider local. Không tính năng nào bắt buộc tài khoản cloud.
**Lý do:** chi phí gần 0 sau khi hoàn thành nền tảng là một phần của Vision; quyền riêng tư; và độc lập với sự tồn tại của nhà cung cấp.
**Hệ quả:** chấp nhận chất lượng thấp hơn ở một số tác vụ; cần chế độ degraded cho máy yếu; phải quản lý model local trên đĩa.
**Đã loại:** cloud-first có cache local (rẻ hơn để xây, nhưng phá vỡ cả Vision lẫn P2).

---

## ADR-0008 — Kiểm định chất lượng lai: tất định trước, LLM sau
**Trạng thái:** Accepted

**Quyết định:** rubric gồm tiêu chí `deterministic` và `llm`; luôn chạy tất định trước với `fail_fast`; LLM judge phải khác provider đã sinh ra artifact.
**Lý do:** kiểm tất định rẻ, nhanh, ổn định và không gian lận được; LLM chỉ dùng cho phán đoán thật sự cần. Judge khác generator để tránh tự khen.
**Hệ quả:** rubric phải được viết cẩn thận và hiệu chuẩn định kỳ; `edit_rate` là chỉ số thật, điểm số chỉ là chỉ số phụ.
**Đã loại:** chỉ dùng LLM judge (tốn kém, không ổn định) · chỉ dùng kiểm tất định (không bắt được lỗi ngữ nghĩa).

---

## ADR-0009 — Kernel cấm mọi phụ thuộc AI, cưỡng chế bằng CI
**Trạng thái:** Accepted · **Không thể thương lượng**

**Quyết định:** `kernel/` không được import bất kỳ SDK AI nào; CI grep chặn PR vi phạm; thêm một job CI xóa `providers/` và `agents/` rồi build + test Kernel.
**Lý do:** ranh giới không được cưỡng chế bằng máy sẽ bị xói mòn bằng con người (RSK-03). Đây là biện pháp kỹ thuật duy nhất bảo vệ được mục tiêu 10 năm.
**Hệ quả:** đôi khi phải viết thêm lớp trung gian cho những thứ "hiển nhiên"; chấp nhận.
**Đã loại:** dựa vào kỷ luật cá nhân (đã thất bại trong mọi dự án dài).

---

## ADR-0010 — Chính sách là dữ liệu, không phải code
**Trạng thái:** Accepted

**Quyết định:** routing weight, budget, time window, permission, energy threshold đều nằm trong YAML dưới `.paos/policies/`, có version, hot-reload.
**Lý do:** hành vi hệ thống cần điều chỉnh được mà không deploy; policy có version giúp Decision Record tái lập được; và bạn có thể thử nghiệm profile khác nhau mà không đụng code.
**Hệ quả:** cần validate schema policy khi load; cần ghi `policy_version` vào mọi Decision Record.

---

## ADR-0011 — v1 là một người dùng, một máy
**Trạng thái:** Accepted

**Quyết định:** không thiết kế cho multi-user, multi-tenant, đồng bộ nhiều máy ở v1.
**Lý do:** mọi khái niệm multi-user (auth, phân quyền theo user, khóa phân tán, đồng bộ xung đột) sẽ nhân đôi độ phức tạp cho một nhu cầu chưa tồn tại. Đây là ứng dụng trực tiếp của Anti-goals.
**Hệ quả:** nếu sau này cần, phải bổ sung `owner_id` và tầng đồng bộ — chấp nhận chi phí đó **khi nào nhu cầu là thật**.

---

## ADR-0012 — Xóa mềm: Trash thay vì xóa cứng
**Trạng thái:** Accepted

**Quyết định:** không có thao tác xóa cứng nào trong hệ thống. Xóa = chuyển vào `trash/YYYY-MM-DD/`, dọn tự động sau 30 ngày, có `restore`.
**Lý do:** hệ thống tự động chạy không giám sát + thao tác không hoàn tác = công thức mất dữ liệu. Chi phí đĩa rẻ hơn nhiều so với mất một Project.
**Hệ quả:** cần job dọn rác và báo cáo dung lượng; thao tác dọn Trash vĩnh viễn là tier CONFIRM.

---

## ADR-0013 — Artifact bất biến, sửa = tạo bản mới
**Trạng thái:** Accepted

**Quyết định:** artifact không bao giờ bị ghi đè; phiên bản mới trỏ về bản cũ qua `supersedes`.
**Lý do:** cho phép so sánh phiên bản, truy vết vòng self-correction, tính `edit_rate`, và khôi phục khi bản mới tệ hơn bản cũ — một tình huống rất thường gặp với AI.
**Hệ quả:** tốn đĩa hơn; cần chính sách nén/dọn bản cũ với artifact lớn (video) sau N ngày.

---

## ADR-0014 — Trace và Decision Record là bắt buộc, không phải tùy chọn
**Trạng thái:** Accepted

**Quyết định:** mọi Process sinh trace đầy đủ; 4 loại quyết định (workflow, provider, retry, hoãn) bắt buộc sinh Decision Record. Không có chế độ tắt.
**Lý do:** Explainability là một trong những nguyên tắc gốc (P5), và trace chính là dữ liệu thô để sinh Operational Knowledge — tài sản dài hạn của dự án. Tắt trace = ngừng tích lũy tài sản.
**Hệ quả:** overhead lưu trữ (~5–15% kích thước DB); cần rollup/nén event progress sau 30 ngày.

---

## ADR-0021 — Framework web chỉ sống ở `apps/paosd/`
**Trạng thái:** Accepted · 2026-08 · Quyết định Ngày 0 ([doc 18 §3](18-day0-implementation-playbook.md))

**Bối cảnh:** doc 02 §8 mô tả `paosd` là "Kernel + HTTP API cục bộ". Nếu không tách bạch, dễ trôi thành việc `kernel/` import thẳng FastAPI để "tiện" — vi phạm P1 kiểu gián tiếp: Kernel không biết gì về AI, nhưng nếu nó biết về HTTP request/response của một framework cụ thể thì ranh giới cũng đã bị xói mòn theo cách tương tự.
**Quyết định:**
1. `apps/paosd/` là tiến trình duy nhất được phép khởi động một HTTP server.
2. Toàn bộ mã HTTP (routes, request/response model, middleware) sống trong `apps/paosd/`; `kernel/` chỉ lộ ra API Python thuần (hàm/coroutine + dataclass), không import `fastapi`/`starlette`/`uvicorn`.
3. `apps/paosd/` là lớp mỏng dịch HTTP ⇄ lời gọi Kernel — không chứa logic nghiệp vụ, chỉ validate request/response và gọi hàm Kernel tương ứng.
**Lý do:** giữ Kernel test được mà không cần khởi động server; cho phép thay framework web (hoặc thêm gRPC/CLI trực tiếp) mà không chạm Kernel; cưỡng chế được bằng `import-linter` (contract `kernel-khong-web`) thay vì kỷ luật cá nhân.
**Hệ quả:** mọi endpoint mới cần một hàm Kernel tương ứng lộ ra trước, rồi mới bọc route — không viết logic nghiệp vụ trực tiếp trong route handler.
**Đã loại:** Kernel tự host HTTP server nội bộ (gọn hơn ngắn hạn, nhưng khóa cứng Kernel vào một framework, vi phạm tinh thần P1) · gộp `apps/paosd/` và `kernel/` thành một package (mất khả năng test Kernel độc lập, MNT-06).

---

## ADR-0022 — Hợp đồng liên tầng validate bằng JSON Schema
**Trạng thái:** Accepted · 2026-08 · Quyết định Ngày 0

**Bối cảnh:** doc 04 định nghĩa Capability I/O, Event payload, Policy đều là dữ liệu có cấu trúc đi qua ranh giới tiến trình (Kernel ⇄ Provider ⇄ Plugin, có thể khác ngôn ngữ theo ADR-0005). Chỉ dựa vào type hint Python (Pydantic/dataclass) không đủ vì không tự mô tả được ở ranh giới ngoài ngôn ngữ.
**Quyết định:** mọi hợp đồng dài hạn (Capability Contract, Event Schema) được định nghĩa chính thức bằng JSON Schema (`capabilities/*.yaml` chứa `input_schema`/`output_schema`, `schemas/events/*.schema.json`); validate lúc chạy ở ranh giới (trước `invoke()`, trước `publish()` event). Type hint Python là lớp thứ hai cho riêng nội bộ Kernel/SDK, không thay thế validate JSON Schema.
**Lý do:** JSON Schema đọc được không cần Python, công cụ hỗ trợ rộng, cho phép Provider viết bằng ngôn ngữ khác (ADR-0005) tự validate phía họ; và là cơ sở để Gate 5 (`check-event-schemas.py`) cưỡng chế "mọi event phát ra đều có schema đã đăng ký" một cách máy móc, không cần đọc code Python.
**Hệ quả:** thêm field vào schema phải nghĩ tới "optional = không tăng version, đổi/xóa = tăng version" (doc 04 §6) ngay từ đầu; overhead validate lúc chạy (chấp nhận được, không phải hot path theo PERF-01).
**Đã loại:** chỉ dùng Pydantic model làm hợp đồng (khóa cứng vào Python, Provider ngôn ngữ khác không tự kiểm được) · Protobuf (mạnh về hiệu năng nhị phân nhưng không cần ở quy mô một máy, và kém thân thiện khi đọc/sửa tay so với JSON/YAML — vi phạm P11 Boring technology).

---

## ADR-0023 — ULID có tiền tố cho ID; PID số nguyên riêng cho Process
**Trạng thái:** Accepted · 2026-08 · Quyết định Ngày 0 · Chi tiết kỹ thuật ở doc 18 D-02

**Bối cảnh:** doc 03 §1.1 đã quy ước `job_`, `proc_`, `task_`... + ULID nhưng chưa chốt cách sinh ID và cách cấp PID (số nguyên tăng dần, cho con người) một cách an toàn dưới ghi đồng thời.
**Quyết định:** mọi entity ID = `<tiền_tố>_` + ULID (sắp xếp được theo thời gian, 128-bit, không cần round-trip tới DB để sinh). Riêng `Process.pid` là số nguyên tăng dần, cấp phát bằng `UPDATE counters SET value = value + 1 ... RETURNING value` trong cùng transaction tạo Process (không dùng `AUTOINCREMENT` của SQLite trực tiếp trên bảng `processes`, để tránh lộ chi tiết implementation và để có thể reset counter theo namespace nếu cần sau này).
**Lý do:** ULID cho máy (sắp xếp được, phân tán sinh được, không lộ thông tin nhạy cảm như số thứ tự tuyệt đối); PID số nguyên cho người (dễ gõ, dễ nhớ, giống thói quen `ps`/Task Manager — doc 00 §4 mượn triết lý OS thật).
**Hệ quả:** cần bảng `counters` riêng (namespace `pid`); mọi nơi hiển thị cho người dùng ưu tiên PID, log/API nội bộ dùng ULID.
**Đã loại:** dùng toàn bộ UUID4 (không sắp xếp được theo thời gian, khó đọc trong log) · dùng `AUTOINCREMENT` của SQLite làm cả ID lẫn PID (lộ trực tiếp cấu trúc bảng ra ngoài, khó tách khỏi SQLite sau này nếu cần).

---

## ADR-0024 — SQLite qua single-writer actor, không ghi trực tiếp từ nhiều coroutine
**Trạng thái:** Accepted · 2026-08 · Quyết định Ngày 0

**Bối cảnh:** ADR-0002 chọn SQLite (WAL) làm state store. WAL cho phép nhiều reader + 1 writer đồng thời ở mức tiến trình, nhưng trong một tiến trình `paosd` bất đồng bộ, nhiều coroutine có thể cùng cố ghi, dẫn tới lock contention khó tái lập và khó debug.
**Quyết định:** mọi lệnh ghi vào `state.db` đi qua đúng một **single-writer actor** (một coroutine sở hữu kết nối ghi, nhận lệnh qua queue nội bộ) thay vì mỗi coroutine tự mở transaction ghi. Đọc vẫn có thể song song qua kết nối read-only riêng. Điều khoản 3: không transaction nào được kéo dài qua một `await` gọi ra ngoài tiến trình (gọi Provider, HTTP, subprocess) — mở transaction, làm việc thuần trong-process, commit, rồi mới `await` việc ngoài.
**Lý do:** loại bỏ hẳn một lớp race condition thay vì cố xử lý retry-on-lock; giữ transaction ngắn giúp WAL không phình, giảm nguy cơ một Provider chậm khóa cả Kernel ghi state.
**Hệ quả:** mọi thao tác ghi phải qua actor này (không có đường tắt gọi thẳng `sqlite3`/`aiosqlite` ở nơi khác — cưỡng chế bằng "không truy cập `state.db` ngoài `kernel/state/`", doc 17 §3); cần thiết kế API actor đủ tổng quát để không thành nút thắt hiệu năng (SCL-01).
**Đã loại:** mỗi coroutine tự mở connection + retry khi `SQLITE_BUSY` (đơn giản hơn lúc đầu nhưng dễ có race, khó test tất định) · chuyển sang PostgreSQL để có write concurrency tốt hơn (thừa cho quy mô 1 máy, vi phạm ADR-0002 và P11).

---

## ADR-0025 — `click` cho `paosctl`
**Trạng thái:** Accepted · 2026-08 · Quyết định lát cắt 5c ([doc 18 D-07](18-day0-implementation-playbook.md))

**Bối cảnh:** `paosctl` (doc 04 §1) cần một thư viện CLI cho 6 lệnh con (`run`, `ps`, `status`, `explain`, `events tail`, `doctor`), gọi HTTP tới `paosd` — không tự viết parser tay vì sẽ phình dần khi thêm lệnh (M1+).
**Quyết định:** dùng `click>=8.1`. `apps/paosctl/` chỉ gọi `httpx` tới `paosd` (127.0.0.1:8787) — không bao giờ import `kernel/` trực tiếp (doc 04 §1: "CLI và UI chỉ dùng API này, không truy cập DB trực tiếp").
**Lý do:** ổn định lâu năm, ít phụ thuộc, hỗ trợ group lệnh con (`events tail`) và test được qua `click.testing.CliRunner` mà không cần subprocess — đúng tinh thần P11 (Boring technology) hơn các framework CLI mới hơn (typer thêm phụ thuộc pydantic dù đã có sẵn, nhưng ràng buộc version-lockstep không cần thiết).
**Hệ quả:** lệnh con mới phải thêm cả ở đây lẫn cập nhật doc 04 nếu đổi hình dạng API.
**Đã loại:** `argparse` (đủ dùng nhưng không có ergonomics cho group lệnh con + test runner tiện như click) · `typer` (thêm ràng buộc phiên bản pydantic không cần thiết cho một CLI mỏng).

---

## ADR-0026 — `POST /v1/jobs` trả về khi QUEUED, không đợi Agent chạy xong
**Trạng thái:** Accepted · 2026-08 · Quyết định M1-2 ([doc 19 P-M1-2](19-prompt-library.md)), trả nợ RSK-21 ([doc 14](14-risk-register.md))

**Bối cảnh:** Runner M0 (lát 5c) chạy toàn bộ Agent — kể cả gọi provider và ghi artifact — bên trong `EventBus.dispatch()` mà `ProcessManager.create()` `await` trước khi trả về, khiến `POST /v1/jobs` block tới khi Agent xong. Chấp nhận được với `StubAdapter` (<1s) nhưng khoá cứng kiến trúc sai hướng trước khi M1-3 (3 Process song song) cần một Runner thật sự bất đồng bộ.
**Quyết định:** `POST /v1/jobs` chỉ đảm bảo Process đã qua `CREATED → PLANNING → QUEUED` (2 lần ghi DB nhanh, vẫn đồng bộ) trước khi trả response — KHÔNG còn đảm bảo Agent đã chạy xong. Thực thi Agent chuyển sang `Runner.worker_loop()` chạy nền, tiêu thụ một `asyncio.Queue` nội bộ. Client (CLI, HTTP caller bất kỳ) phải `GET /v1/processes/{pid}` để poll, hoặc theo dõi `events tail`/`explain`.
**Lý do:** đây là bước tối thiểu để có Runner bất đồng bộ thật mà không cần xây DAG Scheduler đầy đủ ngay (đó là M1-3) — tách rõ "job đã được nhận" khỏi "job đã xong" là tiền đề bắt buộc cho chạy song song.
**Hệ quả:** mọi caller (kể cả `paosctl run`) phải tự poll — `paosctl run` đã viết vòng lặp poll từ M0 (phòng thủ trước khi cần tới), không cần sửa. Test nào check trạng thái ngay sau `POST` phải đổi sang poll tới terminal. `asyncio.Queue` không sống sót qua restart daemon — bù bằng quét lại `processes` ở trạng thái QUEUED lúc `build_daemon()` khởi động (không cần bảng hàng đợi bền riêng ở quy mô M1).
**Đã loại:** giữ nguyên đồng bộ, chờ tới M1-3 mới sửa một lần (dồn 2 thay đổi lớn vào 1 lát cắt, khó review, khó tách lỗi nếu có) · dùng framework queue ngoài (Celery/RQ) — thừa cho quy mô 1 máy, vi phạm P11 (Boring technology) và ADR-0024 (SQLite qua single-writer actor đã đủ).

---

## Backlog ADR (chưa quyết định, cần trước milestone tương ứng)

| Dự kiến | Chủ đề | Cần trước |
|---|---|---|
| ADR-0015 | Chọn thư viện vector search cụ thể + mô hình embedding | M5 |
| ADR-0016 | Chiến lược chunking tài liệu dài | M5 |
| ADR-0017 | Công nghệ UI (Web local vs Tauri) | M8 |
| ADR-0018 | Định dạng và cơ chế phân phối plugin | M8 |
| ADR-0019 | Chiến lược đồng bộ nhiều máy | v2 |
| ADR-0020 | Chữ ký số cho plugin | v2 |

**Về thứ tự số:** ADR-0015→0020 được đánh số trước (đặt chỗ khi backlog được nhận diện) nhưng quyết định sau, đúng lúc milestone cần. ADR-0021→0024 lại được **quyết định sớm hơn** — chúng là các quyết định kỹ thuật bắt buộc phải chốt ngay ở Ngày 0 để M0 có nền để đứng ([doc 18 §3](18-day0-implementation-playbook.md)), nên số ADR không đơn điệu theo thời gian chốt. Số ADR chỉ là định danh duy nhất, không phải thứ tự thời gian — đọc **Trạng thái** và ngày để biết cái nào đã Accepted.
