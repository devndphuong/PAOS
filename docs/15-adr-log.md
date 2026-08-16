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

## ADR-0015 — `sqlite-vec` cho vector search (xác nhận ADR-0002); `bge-m3` qua Ollama cho embedding
**Trạng thái:** Accepted · 2026-08 · Quyết định P-M5-0 ([doc 19](19-prompt-library.md)), chốt trước M5

**Bối cảnh:** doc 07 §3 (Truy hồi) đặt "Vector search (top-k=8, ngưỡng cosine ≥ 0.62)" là bước 3 của chiến lược lai. Doc 03 §3 đã "chừa cột" `memory_vectors(memory_id, embedding BLOB, model TEXT, dim INTEGER)` với chú thích `sqlite-vec`, và ADR-0002 đã nhắc "vector search qua sqlite-vec" như một hệ quả phụ — nhưng chưa từng có ADR riêng cân nhắc phương án, và chưa chọn MODEL embedding cụ thể. Quy mô dữ liệu là MỘT người dùng, MỘT máy (ADR-0011): L3 (sở thích cá nhân) cỡ hàng trăm mục; L4 (World Cache — tài liệu đã đọc, đã chunk theo ADR-0016) cỡ vài nghìn tới thấp chục nghìn chunk sau nhiều năm sử dụng thật — không phải quy mô cần ANN (approximate nearest neighbor) thật sự.

**Quyết định:**
1. **Vector search: `sqlite-vec`** (xác nhận lại ADR-0002, không đổi). Dùng virtual table `vec0` của `sqlite-vec` lưu ngay trong `state.db` hiện có; tìm kiếm brute-force (flat, chính xác 100%, không xấp xỉ) trên cosine similarity — đủ nhanh (<50ms ước tính) ở quy mô vài chục nghìn vector 1024 chiều trên CPU thường.
2. **Embedding mặc định: `bge-m3`** (BAAI, 568M tham số, 1024 chiều, hỗ trợ >100 ngôn ngữ bao gồm tiếng Việt tốt), chạy qua provider Ollama đã có sẵn từ M0 (`providers/ollama/`) — capability mới `text.embed@1` (song song `text.generate@1`, cùng lớp Capability). Chạy 100% local (LOC-03), không cần tài khoản cloud.
3. `memory_vectors.model`/`dim` lưu kèm mỗi vector để PHÁT HIỆN lệch model — đổi embedding model bắt buộc re-embed toàn bộ (không âm thầm trộn vector từ 2 model khác nhau vào cùng 1 lần tìm kiếm).

**Lý do:** nội dung PAOS (script video, ghi chú, tài liệu người dùng đọc) chủ yếu **tiếng Việt** — chất lượng đa ngôn ngữ là tiêu chí quyết định, không phải tốc độ thô; `bge-m3` xếp hạng cao trên benchmark đa ngôn ngữ (MTEB) và có hỗ trợ tiếng Việt thật, hơn hẳn các model embedding phổ biến khác vốn tối ưu chủ yếu cho tiếng Anh. Ở quy mô dữ liệu MỘT người dùng, chi phí "chậm hơn ANN" của brute-force sqlite-vec là không đáng kể so với lợi ích "0 dịch vụ nền, 1 file trạng thái duy nhất" (ADR-0002/ADR-0007/P11).

**Hệ quả:** cần định nghĩa capability mới `text.embed@1` (`capabilities/text.embed/1/`, input_schema/output_schema theo ADR-0022) — việc này thuộc P-M5-1, ADR này chỉ CHỐT lựa chọn, chưa triển khai. `bge-m3` (568M) nặng hơn các lựa chọn nhẹ đã cân nhắc — trên máy không GPU/GPU yếu (xem `docs/environment-baseline.md`), embed qua CPU sẽ chậm hơn model nhỏ; chấp nhận được vì phần lớn việc embed xảy ra ở Consolidation Job chạy đêm (doc 07 §5.2), không chặn tương tác trực tiếp của người dùng — chỉ embed CÂU TRUY VẤN (ngắn, tức thời) mới nằm trên đường tương tác, và 1 câu ngắn embed nhanh bất kể model. Đổi model sau này = job backfill re-embed toàn bộ `memory_vectors` (chưa thiết kế job đó ở ADR này).

**Đã loại — vector search:**
1. **FAISS** — thư viện ANN chuẩn công nghiệp, hiệu năng đỉnh cao ở quy mô triệu vector, rất trưởng thành. Loại vì không có persistence built-in (phải tự viết lớp lưu/khôi phục index song song `state.db`, mất tính "1 file = trạng thái" của ADR-0002), biên dịch native phức tạp hơn trên Windows, và lợi ích ANN không bù được overhead vận hành ở quy mô vài chục nghìn vector — vi phạm P11 (Boring technology).
2. **Vector DB ngoài (Qdrant/Weaviate/Milvus)** — mạnh, API rõ ràng, scale ngang tốt cho hệ thống nhiều người dùng. Loại vì đòi thêm một tiến trình dịch vụ chạy song song `paosd` — đúng loại rủi ro ADR-0002/ADR-0007 (0 dịch vụ nền) và ADR-0011 (1 người, 1 máy) đã cố tránh từ đầu; hoàn toàn thừa cho quy mô dữ liệu thật.
3. **usearch** — thư viện ANN nhúng nhẹ hơn FAISS, header-only, có binding Python, không cần dịch vụ riêng. Loại vì vẫn là một index RIÊNG ngoài SQLite (thêm 1 định dạng file cần tự đồng bộ với `state.db` — sai lúc restore/backup dễ làm 2 nguồn lệch nhau), trong khi `sqlite-vec` cho persistence "miễn phí" ngay trong file đã có, đúng tinh thần ADR-0002.

**Đã loại — embedding model:**
1. **`all-MiniLM-L6-v2`** — cực nhẹ (22M tham số), rất nhanh trên CPU, chuẩn phổ biến trong RAG tiếng Anh. Loại vì huấn luyện chủ yếu tiếng Anh — tiếng Việt yếu; 384 chiều thấp giảm khả năng phân biệt ngữ nghĩa cho nội dung đa dạng tích luỹ nhiều năm.
2. **`nomic-embed-text`** (qua Ollama) — context dài (8192 token), model embedding phổ biến nhất trong hệ sinh thái Ollama, nhẹ hơn `bge-m3` đáng kể. Loại vì cũng tối ưu chủ yếu cho tiếng Anh, đa ngôn ngữ yếu hơn rõ rệt so với `bge-m3` trên benchmark MTEB đa ngôn ngữ — không đạt tiêu chí quyết định (chất lượng tiếng Việt).
3. **Cloud embedding API (OpenAI `text-embedding-3-small`, Cohere `embed-multilingual-v3`)** — chất lượng/đa ngôn ngữ tốt nhất hiện có, không tốn tài nguyên máy cá nhân. Loại làm MẶC ĐỊNH vì Memory L3 tuyệt đối không được rời máy khi `privacy: private` (doc 07 §6, ADR-0007/P2) — vi phạm ngay từ nguyên tắc gốc nếu dùng cho L3. Có thể để ngỏ như một provider CLASS=cloud thay thế CHỈ cho L4 World Cache có `privacy: shared` — không phải phạm vi quyết định của ADR này, cân nhắc lại ở M5 nếu có nhu cầu thật.

**Cập nhật khi triển khai (P-M5-1, 2026-08-16):** mục 1 của Quyết định ("dùng virtual table `vec0`") được CHỈNH LẠI thành: dùng **bảng thường** `memory_vectors` (đúng schema doc 03 §3, không đổi) + hàm vô hướng `vec_distance_cosine()` mà `sqlite-vec` cung cấp — KHÔNG dùng virtual table `vec0`. Lý do phát hiện lúc code thật: mọi ví dụ `vec0` trong tài liệu chính thức của `sqlite-vec` (kể cả bản có `distance_metric=cosine`) chỉ dùng `INTEGER PRIMARY KEY` (rowid alias) cho khoá chính — không có ví dụ hay xác nhận nào cho `TEXT PRIMARY KEY`, trong khi mọi ID của dự án là TEXT có tiền tố ULID (ADR-0023). Ép `memory_id` (TEXT) vào `vec0` sẽ phải tạo thêm một tầng ánh xạ integer↔TEXT không cần thiết. Tài liệu `sqlite-vec` chính thức (`site/features/knn.md`) LIỆT KÊ RÕ cách "thủ công" (bảng thường + `vec_distance_L2()`/`vec_distance_cosine()` + `ORDER BY`) là một phương án chính thức, không phải hack — khớp NGUYÊN VĂN tinh thần "brute-force, chính xác 100%" đã chọn ở mục Quyết định #1, không đổi bản chất quyết định (vẫn `sqlite-vec`, vẫn brute-force, vẫn cosine, vẫn 1 file `state.db`) — chỉ đổi CÚ PHÁP SQL cụ thể. Triển khai: `kernel/state/db.py::_load_vec_extension()`, `apps/paosd/memory_retriever.py`.

---

## ADR-0016 — Chunking: cửa sổ token cố định có overlap, hàm thuần tái lập được
**Trạng thái:** Accepted · 2026-08 · Quyết định P-M5-0 ([doc 19](19-prompt-library.md)), chốt trước M5

**Bối cảnh:** doc 01 mô tả luồng xử lý tài liệu dài "OCR → chunk → dịch → tóm tắt" (ingest vào L4 World Cache, doc 07 §1) — văn bản dài phải chia nhỏ trước khi embed (ADR-0015), vừa vì giới hạn ngữ cảnh thực tế của việc tạo ra một vector "có ý nghĩa" (nhồi cả tài liệu vào 1 vector làm loãng ngữ nghĩa), vừa để đơn vị truy hồi (doc 07 §3) đủ nhỏ, đủ liên quan. Ràng buộc bắt buộc từ doc 19 P-M5-0: **chunking phải tái lập được — cùng tài liệu, cùng cấu hình → cùng chunk**, mọi lần, mọi máy.

**Quyết định:** chunking theo **cửa sổ token cố định có overlap** (fixed-size token window), KHÔNG theo ranh giới ngữ nghĩa (câu/đoạn văn) ở v1:
- `chunk_size` mặc định **512 token** (đo bằng tokenizer CỦA CHÍNH embedding model đã chọn ở ADR-0015 — XLM-RoBERTa tokenizer của `bge-m3`, giới hạn thật của model là 8192 token nhưng 512 giữ mỗi chunk tập trung 1 ý, khớp thực hành phổ biến cho RAG).
- `overlap` mặc định **64 token** (~12.5%) — chunk kế tiếp lặp lại 64 token cuối của chunk trước, giảm mất ngữ cảnh khi một câu/ý bị cắt đúng ở ranh giới.
- Cắt theo **ranh giới từ** (tách theo khoảng trắng trước khi gộp token) — không bao giờ cắt giữa một từ, đặc biệt quan trọng với tiếng Việt (tổ hợp dấu thanh dễ vỡ nếu cắt sai vị trí byte/ký tự).
- Chunking là **hàm thuần**: `chunk(text, config) -> list[Chunk]`, không đọc trạng thái ẩn, không phụ thuộc thời điểm chạy hay phiên bản model đang dùng lúc gọi — cùng `text` + cùng `config` → CÙNG kết quả tuyệt đối, mọi lúc, mọi máy.
- Re-chunk khi VÀ CHỈ KHI: nội dung tài liệu đổi (hash nội dung khác) HOẶC `chunk_size`/`overlap` đổi (một `chunk_config_version` lưu kèm để nhận diện lệch cấu hình, cùng tinh thần `memory_vectors.model` ở ADR-0015).

**Lý do:** cửa sổ cố định là baseline đơn giản nhất đạt được yêu cầu tái lập TUYỆT ĐỐI — bất kỳ hình thức "chunking ngữ nghĩa" nào dùng LLM để tự quyết định ranh giới ý đều không tất định (đổi provider/model qua thời gian → chunk khác nhau cho CÙNG tài liệu, phá vỡ khả năng so sánh/rebuild). Overlap 12.5% đánh đổi một lượng nhỏ dung lượng lưu trùng lặp để giảm đáng kể rủi ro mất ngữ cảnh ở biên — hợp lý ở quy mô dữ liệu cá nhân.

**Hệ quả:** chunk có thể cắt giữa câu/đoạn văn tự nhiên — chất lượng truy hồi ở biên chunk kém hơn semantic chunking lý tưởng; chấp nhận cho v1, nâng cấp khi có bằng chứng thật đo được vấn đề (P4 — chưa có 2 ca dùng thật cần semantic chunking). Tokenizer gắn chặt với embedding model đã chọn (ADR-0015) — đổi embedding model kéo theo đổi tokenizer, bắt buộc re-chunk TOÀN BỘ trước khi re-embed (thứ tự: re-chunk → re-embed, không thể chỉ re-embed với chunk cũ nếu tokenizer đổi).

**Đã loại:**
1. **Semantic chunking (tách theo câu/đoạn văn bằng NLP, hoặc để LLM tự quyết định ranh giới ý)** — chất lượng truy hồi tốt hơn ở biên chunk, giữ nguyên vẹn một ý trong một chunk. Loại ở v1 vì: dùng LLM phá vỡ thẳng yêu cầu tái lập được (không tất định qua thời gian/model); ngay cả dùng NLP tất định (vd sentence tokenizer tiếng Việt) thì độ phức tạp thêm (cần thư viện NLP tiếng Việt riêng, xử lý viết tắt/số thứ tự dễ nhầm ranh giới câu) không tương xứng lợi ích đo được ở quy mô dữ liệu cá nhân hiện tại.
2. **Chunk theo số KÝ TỰ thay vì token** — đơn giản hơn (không cần tokenizer lúc chunk, tách rời khỏi lựa chọn model), tính toán rẻ hơn. Loại vì tiếng Việt có mật độ ký tự/token khác biệt đáng kể so với tiếng Anh (dấu thanh, âm ghép, từ Hán-Việt) — cùng số ký tự có thể chênh lệch số token đáng kể giữa các đoạn văn khác nhau, khiến chunk không đồng đều về "lượng ngữ nghĩa" thật sự đưa vào embedding model, triệt tiêu chính lợi ích của việc giới hạn theo token.
3. **Không overlap (cắt liền mạch, non-overlapping)** — đơn giản nhất, không tốn thêm dung lượng lưu trùng lặp giữa các chunk. Loại vì mất ngữ cảnh nghiêm trọng khi một ý quan trọng bị cắt đúng ở ranh giới chunk — ý đó bị chia đôi, không chunk nào giữ đủ ngữ cảnh để truy hồi đúng; đánh đổi ~12.5% dung lượng lấy overlap là hợp lý so với rủi ro mất thông tin.

---

## ADR-0017 — UI v1: Web local tĩnh phục vụ bởi `paosd`, không framework/build step, không Tauri

**Trạng thái:** Accepted · 2026-08 · Quyết định P-M8-0 ([doc 19](19-prompt-library.md)), chốt trước M8

**Bối cảnh:** [doc 02](02-architecture.md) §8 đã phác sẵn từ Ngày 0 "Web UI — tĩnh, gọi paosd" trong sơ đồ triển khai, nhưng chưa ADR nào chốt CÔNG NGHỆ cụ thể — bảng backlog ADR (đặt chỗ từ P-M5-0) đặt đúng câu hỏi "Web local vs Tauri". [doc 10](10-observability-and-explainability.md) §8 giới hạn phạm vi UI v1 CHÍNH XÁC 4 màn hình (Processes/Explain/Projects/Knowledge), "không hơn" — không cần một app native đầy đủ tính năng. [doc 11](11-nfr-and-slo.md) §7 UX-04 ("CLI có đủ 100% chức năng trước khi UI được xây") đã ĐÚNG từ trước — `paosctl` làm được mọi việc UI sẽ làm — nghĩa là UI chỉ là LỚP HIỂN THỊ THÊM, hạ tầng phía sau không phụ thuộc lựa chọn công nghệ UI theo bất kỳ cách nào. doc 11 §3 LOC-04 ("không cần dịch vụ nền ngoài") + §8 POR-04 ("gỡ cài đặt = xóa 1 thư mục") + doc 02 §8 ("Cài đặt = 1 lệnh, gỡ = xóa 1 thư mục") đặt ràng buộc thật lên lựa chọn. Repo hôm nay KHÔNG có bất kỳ công cụ frontend nào (không `package.json`, không toolchain JS/Rust) — lựa chọn hoàn toàn xanh.

**Quyết định:** **Web local tĩnh** — 1 thư mục HTML/CSS/JS thuần (KHÔNG framework, KHÔNG bundler/build step, KHÔNG npm/`node_modules`), được `apps/paosd/app.py` phục vụ tĩnh (`StaticFiles`) NGAY TRÊN CÙNG process/port đã có (`127.0.0.1:8787`, ADR-0021) — mở `http://127.0.0.1:8787/` trên trình duyệt hệ thống là dùng được UI, gọi thẳng `fetch()` tới `/v1/*` CÙNG origin (không cần CORS). **KHÔNG dùng Tauri.**
- `<script type="module">` load thẳng bởi trình duyệt, KHÔNG qua bước biên dịch nào.
- KHÔNG tải bất kỳ thư viện nào từ CDN — mọi CSS/JS nằm sẵn trong repo, để UI chạy được offline (khớp LOC-01 "chạy trọn workflow offline" — 1 CDN chết mạng sẽ làm UI trắng trang, vi phạm chính NFR này).
- 4 màn hình = 4 module tương ứng doc 10 §8, dùng chung 1 lớp gọi API tới `/v1/processes`, `/v1/processes/{pid}/trace`, `/v1/artifacts`, `/v1/memory`, `/v1/knowledge/graph` — toàn bộ ĐÃ CÓ từ M0–M6 (doc 04 §1), P-M8-4 chỉ cần dựng giao diện, không thêm endpoint mới nào cho phần đọc.

**Lý do:**
1. doc 02 §8 đã phác "Web UI — tĩnh" từ Ngày 0 — chọn Tauri MÂU THUẪN trực tiếp với kiến trúc đã sketch, cần viết lại §8 chứ không chỉ thêm 1 ADR.
2. "Cài đặt = 1 lệnh, gỡ = xóa 1 thư mục" đạt TUYỆT ĐỐI với Web local — UI chỉ là vài file tĩnh, phục vụ bởi process `paosd` ĐÃ chạy sẵn; không thêm 1 binary/installer/process nào.
3. Boring technology (P11) — repo hôm nay 100% Python + vài file YAML/JSON tĩnh. Thêm Tauri = thêm TOÀN BỘ toolchain Rust (compiler, cargo, webview platform-specific) cho một phạm vi đúng 4 màn hình đọc/hiển thị dữ liệu — bất tương xứng.
4. Máy dev thật (`environment-baseline.md`) chưa có Rust/cargo cài sẵn — chọn Tauri đặt thêm rào cản cài đặt ngay lát đầu tiên của M8, cho lợi ích (icon desktop, tray, auto-update native) mà doc 10 §8 không đòi hỏi.
5. Cùng process/port với `paosd` tránh CORS hoàn toàn — không cần `Access-Control-Allow-Origin`, không có 1 tầng "API gateway" giả nào chỉ để 2 origin nói chuyện được.

**Hệ quả:** `apps/paosd/app.py` thêm 1 dòng mount static files ở route `/` (dưới `/v1/*` đã có — không đụng tầng Kernel, không vi phạm ADR-0021 "framework web chỉ sống ở `apps/paosd/`"). Build/deploy UI = commit file tĩnh vào repo, không CI riêng cho frontend. Không có UI framework nghĩa là điều hướng giữa 4 màn hình phải tự viết tay (nhẹ — 4 màn hình cố định không cần router phức tạp, P4). Không có desktop notification/tray icon/auto-update native — ngoài phạm vi doc 10 §8, không mất gì đã cam kết.

**Đã loại:**
1. **Tauri** (đề xuất gốc trong bảng backlog) — desktop app thật, bundle nhẹ hơn Electron, webview hệ thống. Loại vì: (a) mâu thuẫn trực tiếp với doc 02 §8 đã sketch "Web UI — tĩnh"; (b) cần toàn bộ toolchain Rust không có sẵn trên máy dev; (c) build cross-platform (Windows/Mac/Linux) cần hạ tầng CI cross-compile dự án chưa có; (d) lợi ích chính của Tauri (desktop-native feel, tray, single binary) không nằm trong bất kỳ yêu cầu nào ở doc 10 §8/doc 11 — trả giá cho tính năng không ai đòi hỏi.
2. **Electron** — không nằm trong backlog gốc nhưng là ứng viên tự nhiên cùng nhóm. Loại vì nặng hơn Tauri (bundle Chromium + Node đầy đủ, ~150MB+), thêm TOÀN BỘ hệ sinh thái npm/Node.js làm dependency, mâu thuẫn nghiêm trọng hơn với POR-04/LOC-04 (footprint lớn, thường chạy kèm 1 tiến trình Node nền).
3. **SPA framework có build step (React/Vue/Svelte + Vite/webpack)** — quen thuộc, dev experience tốt hơn cho UI lớn dần theo thời gian. Loại ở v1 vì: (a) thêm toàn bộ toolchain npm/`node_modules` cho repo hôm nay 100% Python; (b) 4 màn hình cố định (doc 10 §8 "không hơn") không đủ độ phức tạp để cần state management/component framework; (c) build step nghĩa là "sửa UI" không còn là "sửa 1 file, F5" — chậm vòng lặp phát triển cho lợi ích chưa cần. Có thể revisit nếu UI thật sự phình to sau v1 (P4 — chưa 2 ca dùng thật).
4. **Static file server riêng biệt (khác process với `paosd`, vd `python -m http.server` cổng khác)** — tách rời UI khỏi backend hoàn toàn, "sạch" hơn về phân lớp. Loại vì: (a) cần 2 tiến trình thay vì 1 (mâu thuẫn "cài đặt = 1 lệnh"); (b) 2 origin khác nhau (cổng khác) buộc cấu hình CORS ở `paosd` không vì lý do chính đáng nào, thêm bề mặt tấn công (mở CORS cho origin ngoài) không cần thiết khi cả 2 chạy trên `localhost` của CHÍNH máy người dùng.

---

## ADR-0018 — Plugin phân phối dạng thư mục (local path hoặc git URL), cài vào `workspace/plugins/`, Registry quét thêm 1 gốc

**Trạng thái:** Accepted · 2026-08 · Quyết định P-M8-0 ([doc 19](19-prompt-library.md)), chốt trước M8

**Bối cảnh:** [doc 12](12-plugin-sdk-and-marketplace.md) §1/§6 đã phác plugin là 1 thư mục (`plugins/paos-video/{plugin.yaml, agents/, ...}`) và Phase 1 (v1) "cài từ thư mục local hoặc git URL" — nhưng chưa ADR nào chốt CƠ CHẾ cụ thể: plugin cài xong NẰM Ở ĐÂU trên đĩa, `paosctl plugin install` làm gì từng bước, Registry phát hiện plugin đã cài bằng cách nào lúc khởi động lại. ĐÃ CHỐT SẴN, KHÔNG bàn lại ở ADR này: định dạng manifest (`plugin.yaml`, [doc 04](04-core-contracts.md) §5, hợp đồng dài hạn), cơ chế cách ly lúc CHẠY (subprocess + JSON-RPC qua stdio, ADR-0005), mô hình quyền/sandbox (doc 09 §5, deny-all mặc định). `kernel/registry/registry.py` đã có sẵn tiền lệ nạp động: `Registry.load_adapter()` (importlib, cho Provider) và `Registry.load_agent()` (cho Agent) — cả 2 chạy TRONG CÙNG process; plugin theo ADR-0005 chạy NGOÀI process, nên cần 1 đường nạp mới cho phần thực thi (thuộc P-M8-1), nhưng phần KHAI BÁO (agents/providers/... plugin cung cấp) vẫn là dữ liệu tĩnh Registry quét được y hệt hôm nay.

**Quyết định:**
- **Định dạng phân phối = thư mục thuần** (không đóng gói `.zip`/`.tar.gz`/`.whl`) — cài từ (a) đường dẫn local (copy thư mục) hoặc (b) git URL (`git clone` qua subprocess, không thêm thư viện Python git). Không có định dạng archive nén nào ở v1.
- **Vị trí cài đặt = `workspace/plugins/<plugin_id>/`** — trong `workspace/`, KHÔNG ở gốc repo cạnh `providers/`/`agents/` (những thư mục đó là built-in, đi kèm codebase). Plugin người dùng TỰ cài là DỮ LIỆU NGƯỜI DÙNG, phải theo `workspace/` khi di chuyển máy (POR-05 "di chuyển workspace = copy thư mục" — plugin phải đi cùng).
- **`paosctl plugin install <path-or-git-url>`**: validate `plugin.yaml` đúng schema doc 04 §5 → check `paos_api` range (so khớp version PAOS đang chạy, hàm so semver TỰ VIẾT ~20 dòng, không thêm thư viện `packaging`/`semver`) → in TOÀN BỘ `permissions:` yêu cầu, chờ xác nhận tay (tier CONFIRM, doc 09 §2) → copy/clone vào `workspace/plugins/<id>/` → ghi vào Registry (rescan) → phát `plugin.installed` (schema đã có, doc 05 §3.9) → hot-reload (doc 12 §2, KHÔNG cần khởi động lại `paosd`).
- **Registry quét thêm 1 gốc**: `Registry` (đã nhận `capabilities_dir`/`providers_dir`/`workflows_dir`/`agents_dir`/`rubrics_dir` làm tham số tường minh) nhận thêm `plugins_dir: Path | None = None` — với MỖI thư mục con của `plugins_dir`, đọc `plugin.yaml::provides` rồi quét ĐÚNG các thư mục con nó khai (`agents/`, `providers/`, ...) bằng CHÍNH các hàm scan đã có (`_scan_providers()`, `_scan_agents()`...), không viết logic quét song song mới.
- **`paosctl plugin uninstall <id>`**: xóa `workspace/plugins/<id>/` (giữ Project/Artifact plugin đã tạo, đúng doc 12 §2 "disable/uninstall giữ Project data" — Project sống ở `workspace/projects/`, tách biệt hoàn toàn khỏi `workspace/plugins/`).

**Lý do:**
1. Thư mục thuần (không archive) khớp CHÍNH XÁC ví dụ doc 12 §1 đã có, không cần bịa thêm 1 định dạng đóng gói cho v1 một-người-một-máy (ADR-0011) — archive chỉ có lợi khi cần TRUYỀN QUA MẠNG một marketplace index (Phase 2, doc 12 §6), lúc đó chưa tới.
2. `workspace/plugins/` (không phải gốc repo) giữ đúng ranh giới "workspace = TOÀN BỘ dữ liệu người dùng" (doc 02 §8) — plugin TỰ CÀI khác về bản chất với `providers/`/`agents/` đi kèm codebase; lẫn 2 thứ vào 1 thư mục sẽ làm `git status`/`git diff` của repo dơ theo plugin người dùng cài, và reclone/cài lại PAOS sẽ vô tình XÓA plugin đã cài nếu chúng nằm trong cây repo.
3. Tự viết so sánh semver (~20 dòng) thay vì thêm `packaging`: định dạng `paos_api` doc 04 §5 dùng CHỈ major.minor (`">=1.0,<2.0"`), không cần hỗ trợ pre-release/build-metadata/wildcard đầy đủ của spec PEP 440 — `packaging` giải quyết bài toán RỘNG HƠN nhiều so với nhu cầu thật, thêm 1 dependency (đòi 1 ADR riêng theo quy ước `pyproject.toml` dòng 8) cho ít hơn 20 dòng code tự viết đã đủ.
4. Tái dùng `_scan_providers()`/`_scan_agents()` đã có thay vì viết logic quét mới cho plugin — Registry vốn đã nhận thư mục gốc làm tham số (không hardcode đường dẫn), mở rộng nó quét thêm N thư mục con của `plugins/*/` là thay đổi nhỏ, nhất quán, không tạo 2 con đường "nạp Agent" khác nhau tùy nó đến từ đâu.
5. `git clone` qua subprocess (không thư viện `GitPython`/`pygit2`) — máy dev ĐÃ có `git` cài sẵn (E7, `environment-baseline.md`), dùng lại y hệt cách `ffmpeg` được gọi (subprocess, không phải Python binding) — không thêm dependency Python nào cho 1 tính năng OPTIONAL (cài từ URL), đúng doc 12 §6 "marketplace không bao giờ được trở thành thành phần bắt buộc — PAOS chạy đầy đủ khi offline": cài từ ĐƯỜNG DẪN LOCAL không cần mạng/git; cài từ git URL cần cả 2 nhưng KHÔNG BAO GIỜ là đường bắt buộc.

**Hệ quả:** `kernel/registry/registry.py::Registry.__init__()` thêm tham số `plugins_dir: Path | None = None` (`None` = không quét gì thêm, hành vi hôm nay KHÔNG đổi cho 26+ nơi đã gọi `Registry(...)` — cùng tiền lệ `energy_policy_path`/`time_policy_path` thêm ở P-M7-2/P-M7-3, ADR-0031). `apps/paosctl/__main__.py` thêm nhóm lệnh `plugin install|uninstall|list`. CHƯA làm ở ADR này (thuộc P-M8-1/P-M8-2, lát cắt implementation): cơ chế subprocess+JSON-RPC thật (ADR-0005 đã QUYẾT ĐỊNH nhưng chưa VIẾT CODE), enforce sandbox/permission lúc chạy (doc 09 §5), tự disable khi vi phạm quyền, hot-reload thật sự (Registry rescan không đủ — cần dừng/khởi động lại subprocess plugin cũ nếu đang chạy).

**Đã loại:**
1. **Đóng gói archive (`.zip`/`.tar.gz`) làm định dạng phân phối** — chuẩn hơn cho "1 file duy nhất để chia sẻ", dễ checksum/verify. Loại vì thêm bước nén/giải nén không cần thiết khi v1 chỉ có 2 nguồn cài (thư mục local đã LÀ thư mục, git URL đã tự "đóng gói" qua git) — archive chỉ thật sự cần khi có 1 marketplace index tải file qua HTTP (Phase 2, doc 12 §6), lúc đó quyết định lại, không bịa trước khi cần.
2. **Cài plugin ở gốc repo (`<repo>/plugins/`, cạnh `providers/`/`agents/`)** — đối xứng với cấu trúc đã có, Registry tái dùng nguyên xi các thư mục gốc đã quét mà không cần tham số `plugins_dir` mới. Loại vì lẫn dữ liệu người dùng (plugin tự cài) vào cây mã nguồn — vi phạm ranh giới `workspace/` = dữ liệu người dùng đã có từ doc 02 §8, khiến `git clean`/reclone vô tình xóa plugin người dùng.
3. **Symlink thay vì copy khi cài từ đường dẫn local** — tiết kiệm đĩa, sửa plugin gốc phản ánh ngay không cần cài lại (tiện khi TỰ VIẾT plugin — đúng RSK-13 "v1 chỉ cài plugin do chính bạn viết"). Loại vì Windows (máy dev thật) tạo symlink cần quyền Admin/Developer Mode theo mặc định — thêm rào cản cài đặt không đáng, và POR-05 "di chuyển workspace = copy thư mục" sẽ ĐỨT symlink trỏ ra ngoài workspace khi copy sang máy khác; copy thật giữ cho `workspace/` luôn TỰ ĐỦ (self-contained).
4. **Thêm `packaging` (PyPI) để so `paos_api` semver** — đúng chuẩn, không cần tự viết/tự kiểm edge case. Loại vì (cùng lý do #3 ở trên) — bất tương xứng quy mô, và MỌI dependency mới cần 1 ADR riêng theo quy ước `pyproject.toml` — không đáng cho 1 phép so sánh 2 khoảng số nguyên đơn giản.

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

## ADR-0027 — Biểu thức `${...}` của Workflow: parser tự viết, không `eval`
**Trạng thái:** Accepted · 2026-08 · Quyết định M3-2 ([doc 19 P-M3-2](19-prompt-library.md)), chi tiết hoá ADR-0006

**Bối cảnh:** ADR-0006 đã chốt Workflow là YAML khai báo, biểu thức `${...}` "chỉ đọc dữ liệu, không eval code tùy ý", nhưng chưa chọn CÚ PHÁP cụ thể. Doc 04 §4 cho 2 ví dụ thật cần hỗ trợ: truy cập trường lồng nhau (`${steps.detect.output.has_text_layer == false}`) và toán tử coalesce cho nhánh có thể bị bỏ qua (`${steps.ocr.output.text ?? steps.detect.output.text}`). Đây là nơi RSK-12 (Data ≠ Instruction, doc 09 §4) chạm trực tiếp: nội dung workflow.yaml có thể tới từ plugin bên thứ ba (M8) — bất kỳ hình thức nào cho phép biểu thức chạy được mã Python tuỳ ý đều biến "dữ liệu cấu hình" thành "chương trình không kiểm soát được", đúng thứ ADR-0006 đã cấm.

**Quyết định:** viết một parser đệ quy xuống dòng (recursive-descent) nhỏ, tự tay, cho đúng 1 văn phạm tối giản:
```
expression := comparison | coalesce
comparison := coalesce COMP_OP coalesce      # COMP_OP: == != < <= > >=
coalesce    := operand ("??" operand)*
operand     := path | literal
path        := IDENT ("." IDENT)+            # steps.<id>.output.<field...> | inputs.<field...>
literal     := NUMBER | STRING | "true" | "false" | "null"
```
Không có `&&`/`||` để gộp nhiều điều kiện — muốn logic phức tạp hơn thì viết một Agent (đúng hệ quả đã ghi ở ADR-0006). `path` chỉ đọc qua `dict.get()` từng chặng, trả `None` nếu thiếu (để `??` có cái mà rơi vào) — không có cách nào truy cập thuộc tính Python, gọi hàm, hay import bất cứ thứ gì. `${...}` chỉ được dùng làm TOÀN BỘ giá trị của một field YAML (không nhúng nhiều `${}` xen trong một chuỗi dài hơn) — mọi ví dụ ở doc 04 §4 đều theo hình dạng này.

**Lý do:** an toàn tuyệt đối là thuộc tính CẤU TRÚC, không phải kỷ luật lọc input — parser không có đường nào chạm tới `eval`/`exec`/`compile`/attribute Python thật, nên không có bề mặt tấn công để rà soát, khác hẳn "eval với sandbox" (rủi ro luôn ẩn ở chỗ chưa nghĩ tới). Cũng khớp P11 (Boring technology) — không thêm phụ thuộc ngoài cho một văn phạm nhỏ hơn 40 dòng BNF.

**Hệ quả:** thêm toán tử mới (vd `&&`, hàm `len()`, phép cộng số học) là thay đổi hợp đồng Workflow (doc 04 §4), phải qua ADR mới, không được âm thầm mở rộng parser. `path` trả `None` khi thiếu field (thay vì raise lỗi) là lựa chọn có chủ đích cho `??` — nghĩa là lỗi gõ sai tên field (`step.detect` thay vì `steps.detect`) không tự lộ ra ngay mà âm thầm thành `None`; giảm thiểu bằng validate DAG lúc load workflow (kernel/workflow/spec.py) kiểm path tồn tại trong tập step đã khai báo trước khi Process chạy, không đợi tới lúc `when:` bị đánh giá.

**Đã loại:**
1. **`eval()`/`ast.literal_eval()` với sandbox globals hạn chế** — trông đơn giản nhất, tái dùng cú pháp Python thật (không cần viết parser). Loại vì sandbox `eval()` không phải ranh giới an toàn đáng tin: kỹ thuật thoát sandbox qua chuỗi thuộc tính (`().__class__.__bases__`, `__subclasses__()`...) là tấn công đã biết rộng rãi, và ADR-0006 đã minh thị cấm "eval code tùy ý" — dùng `eval()` dù có lọc cũng vi phạm đúng câu chữ quyết định đó.
2. **Thư viện biểu thức ngoài (`simpleeval`, JMESPath)** — `simpleeval` có API gọn, nhưng bên trong vẫn dựng `eval()` trên AST đã lọc (cùng lớp rủi ro như phương án 1, chỉ khác ai viết bộ lọc). JMESPath an toàn thật (không eval), nhưng là ngôn ngữ truy vấn JSON đầy đủ (slice, filter, hàm dựng sẵn...) — thừa năng lực so với nhu cầu thật (so sánh + coalesce), thêm một phụ thuộc ngoài + một cú pháp người dùng phải học chỉ để dùng 5% khả năng của nó, vi phạm P11.
3. **Parser tự viết (đã chọn)** — khối lượng code nhỏ (~150 dòng), 100% nằm trong tầm kiểm soát/audit của dự án, không phụ thuộc ngoài, và văn phạm tối giản đúng bằng những gì doc 04 §4 cần — không hơn.

---

## ADR-0028 — Trích entity cho Knowledge Graph: danh sách thuật ngữ đã biết, tất định, không LLM
**Trạng thái:** Accepted · 2026-08 · Quyết định P-M5-3 ([doc 19](19-prompt-library.md))

**Bối cảnh:** doc 07 §4 định nghĩa Knowledge Graph cá nhân (12 loại node, 13 loại quan hệ) và yêu cầu `KnowledgeExtractor` tự động biến văn bản artifact (script, plan, summary) thành node/edge có provenance. Doc 13 M5 exit criterion "Rebuild KG từ replay event cho kết quả tương đương" đặt một ràng buộc cứng: toàn bộ pipeline trích xuất phải là **hàm thuần của event log** — cùng chuỗi event, phát lại theo cùng thứ tự, ở bất kỳ máy nào, bất kỳ lúc nào, phải cho ra một đồ thị TƯƠNG ĐƯƠNG. Đây là ràng buộc chưa milestone nào trước đó phải đối mặt trực tiếp ở mức "nội dung tự do do người dùng viết" (khác `sdk/rubric.py`/`sdk/eval.py` vốn chấm điểm, không sinh dữ liệu mới ghi vào một kho tri thức tích luỹ dài hạn).

**Quyết định:** `sdk/kg_extract.py::extract_entities()` trích entity bằng cách khớp CHÍNH XÁC (không phân biệt hoa/thường) một danh sách thuật ngữ đã biết cố định trong code (`KNOWN_TERMS`, ví dụ MongoDB/PostgreSQL/Docker/Database...), KHÔNG gọi ra ngoài, KHÔNG dùng LLM. Quan hệ suy ra được CHỈ có 1 loại: mỗi entity trích từ 1 artifact được gắn cạnh `learned_from` tới node `Source` đại diện artifact đó (đúng nguyên văn ví dụ doc 07 §4.3) — không suy luận `is_a`/`alternative_to`/`depends_on`... giữa các entity cùng xuất hiện trong 1 văn bản.

**Lý do:**
1. **Tất định là điều kiện CẦN, không phải "nên có"** — LLM sinh văn bản không tất định qua thời gian (đổi model/checkpoint/nhiệt độ decode giữa 2 lần chạy cho kết quả khác nhau), vi phạm thẳng exit criterion "rebuild từ replay" ở trên. Cache tuyệt đối kết quả LLM cho MỌI văn bản artifact từng có là không thực tế (là chính bài toán "lưu lại toàn bộ output LLM mãi mãi", không giải quyết gì so với việc không tất định).
2. **Suy luận quan hệ ngữ nghĩa (`is_a`, `alternative_to`...) đòi NLU thật** — một bảng tra cứu tất định không có cách nào biết "PostgreSQL là một lựa chọn thay thế MongoDB" từ câu văn tự do mà không đoán mò/sai nhiều. Giả vờ suy luận được bằng heuristic (vd "2 entity cùng câu → is_a lẫn nhau") sẽ tạo ra rất nhiều cạnh SAI, phá chất lượng đồ thị (doc 07 §4.4) ngay từ lần dùng thật đầu tiên — vi phạm P8 (THẬT THÀ hơn giả vờ).
3. **Độ phủ thấp hơn NER tổng quát là đánh đổi CHẤP NHẬN ĐƯỢC cho v1** — mở rộng `KNOWN_TERMS` là việc lặp lại đơn giản (thêm dòng), không phải rủi ro kiến trúc, đúng tinh thần ADR-0016 (chọn baseline đơn giản, tái lập được, nâng cấp khi có bằng chứng thật đo được cần).

**Hệ quả:** `KnowledgeExtractor` (P-M5-3) sẽ bỏ sót nhiều entity thật chưa có trong danh sách (đặc biệt thuật ngữ mới/hiếm) — chấp nhận được vì độ CHÍNH XÁC của những gì có trong KG quan trọng hơn độ ĐẦY ĐỦ ở giai đoạn này (doc 07 §4.4 "chất lượng đồ thị" đặt trước số lượng). Cơ chế phát hiện mâu thuẫn (`CONTRADICTORY_RELATIONS`, `KnowledgeStore.create_edge()`) được xây SẴN và kiểm bằng test thật, nhưng KHÔNG có đường trích `prefers`/`avoids` thật từ văn bản tự do ở lát cắt này — sẵn sàng cho caller tương lai (M9 Research Plugin, hoặc mở rộng extractor) khi có tín hiệu đáng tin hơn để dùng (doc 07 §2 preference learning từ HÀNH VI quan sát được, không phải parse câu văn, là mô hình đã chọn cho tín hiệu prefers/avoids — xem `apps/paosd/memory_writer.py`).

**Đã loại:**
1. **Capability LLM mới (`text.extract_entities@1`, theo mẫu ADR-0004/ADR-0022)** — nhất quán kiến trúc nhất (mọi truy cập AI qua Capability), tận dụng được model thật đã có (`ollama`/`gpt`) cho chất lượng trích xuất cao hơn nhiều so với bảng tra cố định. Loại vì không tất định (lý do #1 ở trên) trừ khi cache kết quả tuyệt đối theo `sha256(nội dung)` — khả thi về mặt kỹ thuật (giống content-addressed cache đã có, doc 03 §5) nhưng đổi bài toán "trích entity" thành "cache mọi kết quả LLM mãi mãi, không bao giờ re-run dù model tốt hơn ra đời" — một ràng buộc nặng hơn lợi ích thu được ở quy mô artifact hiện tại (script/plan/summary ngắn), có thể cân nhắc lại khi có nhu cầu thật lớn hơn (M9).
2. **Heuristic tổng quát "mọi từ viết hoa là entity" (naive capitalization-based NER)** — không cần danh sách thủ công, tự mở rộng theo nội dung mới. Loại vì độ chính xác thấp, đặc biệt với tiếng Việt (không có quy ước viết hoa danh từ riêng nghiêm ngặt như tiếng Anh, đầu câu/sau dấu chấm dễ nhầm) — sẽ tạo rất nhiều node rác ngay từ lần chạy thật đầu tiên, đúng loại lỗi doc 07 §4.4 muốn tránh.
3. **Thư viện NER tất định có sẵn (spaCy, underthesea cho tiếng Việt)** — chất lượng cao hơn bảng tra tay, vẫn tất định (không gọi LLM). Loại vì thêm phụ thuộc ngoài nặng (model NER vài trăm MB, cần tải/quản lý version riêng biệt khỏi ADR-0015 đã chọn cho embedding) chỉ để giải quyết vấn đề "nhận diện danh từ riêng" mà một bảng tra ~40 mục đã đủ cho quy mô nội dung thật hiện có (script/plan video, không phải kho văn bản khổng lồ) — vi phạm P11 (Boring technology), cân nhắc lại nếu `KNOWN_TERMS` phình to tới mức khó bảo trì tay.

---

## ADR-0029 — `paosctl memory forget`: xóa cứng thật, ngoại lệ có chủ đích với ADR-0012

**Trạng thái:** Accepted · 2026-08 · Quyết định P-M5-4 ([doc 19](19-prompt-library.md))

**Bối cảnh:** doc 07 §6 nói "`paosctl memory list|show|forget <id>` — quyền xóa tuyệt đối thuộc về người dùng." ADR-0012 (đã Accepted trước đó) chốt chính sách xóa mềm CHUNG cho TOÀN hệ thống: "không có thao tác xóa cứng nào trong hệ thống. Xóa = chuyển vào `trash/YYYY-MM-DD/`, dọn tự động sau 30 ngày, có `restore`." Hai quyết định này va nhau trực tiếp ở đúng 1 điểm: nếu `memory forget` tuân ADR-0012 nguyên vẹn, dữ liệu "đã quên" vẫn tồn tại nguyên vẹn, đọc được, khôi phục được trong 30 ngày — mâu thuẫn với chữ "tuyệt đối" mà doc 07 §6 dùng, và đặc biệt nhạy cảm vì đối tượng bị xóa là Memory L3 (sở thích/thông tin CÁ NHÂN, khác Project/Artifact — những thứ ADR-0012 nhắm tới ban đầu không mang tính riêng tư theo cách này).

**Quyết định:** `apps/paosd/memory_store.py::MemoryStore.forget()` thực hiện `DELETE FROM memory_items` + `DELETE FROM memory_vectors` THẬT SỰ, ngay lập tức, không qua `trash/`. Đây là NGOẠI LỆ DUY NHẤT, CÓ CHỦ Ý, với ADR-0012 — giới hạn phạm vi CHÍNH XÁC ở 2 bảng `memory_items`/`memory_vectors` qua đúng 1 API `forget()`/`DELETE /v1/memory/{id}`/`paosctl memory forget`. Mọi thao tác xóa khác trong toàn hệ thống (Project, file, Artifact) vẫn tuân ADR-0012 nguyên vẹn, không đổi.

**Lý do:**
1. **"Quên" chỉ có nghĩa nếu dữ liệu THẬT SỰ biến mất ngay.** Giữ 30 ngày trong Trash (đọc được, restore được) không thỏa mãn kỳ vọng của người dùng khi họ gõ lệnh `forget` cho một mẩu thông tin cá nhân — khác hẳn "xóa Project" (ADR-0012 nhắm đúng ca này: dữ liệu LỚN, mất công tạo lại, và người dùng có thể XÓA NHẦM cần đường lùi) — ở đây người dùng đã tự tay chọn ĐÚNG 1 `memory_id` (qua `paosctl memory list`/`show` trước đó) và gõ lệnh xóa CÓ CHỦ ĐÍCH, không phải thao tác dễ bấm nhầm hàng loạt.
2. **Rủi ro gốc mà ADR-0012 phòng ("hệ thống tự động chạy không giám sát + thao tác không hoàn tác = mất dữ liệu") không áp dụng ở đây.** Không có Agent/Job tự động nào gọi `forget()` — CHỈ người dùng gõ `paosctl memory forget <id>` trực tiếp (đúng mô hình đe dọa doc 09 §1: Permission Guard/Trash tồn tại để chặn HỆ THỐNG hành động sai, không phải để chặn người dùng tự quyết trên chính dữ liệu của họ).
3. **Memory item chưa bao giờ bất biến.** Khác Artifact (ADR-0013, sửa = tạo bản mới có `supersedes`), `MemoryStore.update()` (P-M5-1) đã UPDATE TẠI CHỖ một hàng `memory_items` từ trước (confidence/content trôi dần theo thời gian, không phải chuỗi phiên bản). Xóa hẳn 1 hàng không phá vỡ bất kỳ bất biến nào đã tồn tại — memory chưa từng cam kết lịch sử đầy đủ như Event Log (ADR-0003) hay Artifact.
4. **Mô phỏng Trash cho 1 hàng DB tốn kém hơn lợi ích thu được.** Không có khái niệm "trash/YYYY-MM-DD/" tự nhiên cho 1 row SQLite — làm đúng sẽ cần thêm cột `deleted_at` + sửa MỌI câu query đọc `memory_items` (list_by_tier, get_by_key, vector search SQL trong `memory_retriever.py`...) để lọc bỏ hàng "đã xóa mềm", cho một API ít dùng (forget là hành động hiếm, có chủ đích). Rủi ro quên lọc ở 1 chỗ (để lộ lại dữ liệu "đã quên") cao hơn hẳn lợi ích giữ được khả năng restore 30 ngày mà bản thân tính năng này không cần.

**Hệ quả:** `forget()` KHÔNG có `restore` (khác Trash). CLI (`paosctl memory forget`) yêu cầu xác nhận tay (`click.confirm`, bỏ qua được bằng `--yes` cho script) làm lớp phòng thủ DUY NHẤT trước khi xóa — KHÔNG đi qua `PermissionGuard`/tier CONFIRM chính thức (doc 09 §2 không liệt "xóa Memory" trong bảng CONFIRM; Permission Guard tồn tại để chặn hành động của AGENT/hệ thống tự động, không phải hành động trực tiếp của người dùng qua CLI — cùng lý do #2 ở trên). Event `memory.item.forgotten` phát ra CỐ Ý không mang `content` (chỉ `memory_id`/`tier`/`key`) — Event Log là bất biến (ADR-0003), ghi lại "đã quên" không được vô tình làm lộ lại chính thứ vừa bị quên vào một nơi KHÔNG THỂ xóa. Quyết định này CHỈ áp dụng cho `memory_items`/`memory_vectors` — Knowledge Graph (`kg_nodes`/`kg_edges`) đi NGƯỢC hướng, cố ý không xóa gì (doc 07 §4.4: "Không xóa: dùng `invalidated_at` để giữ lịch sử nhận thức") — `paosctl knowledge forget` KHÔNG được xây ở lát cắt này, xem docs/backlog.md.

**Đã loại:**
1. **Tuân ADR-0012 nguyên vẹn (Trash 30 ngày cho memory forget)** — nhất quán tuyệt đối với toàn hệ thống, không cần ADR ngoại lệ. Loại vì phá vỡ đúng ý nghĩa từ "tuyệt đối" mà doc 07 §6 dùng cho quyền xóa memory — một người dùng lo lắng về quyền riêng tư (đây CHÍNH LÀ lát cắt Privacy Filter) sẽ không chấp nhận "đã xóa nhưng vẫn nằm đọc được 30 ngày" là "quên".
2. **Soft-delete bằng cột `deleted_at` + job dọn định kỳ (thay vì thư mục Trash vật lý)** — nhẹ hơn Trash file thật, vẫn giữ tinh thần "có thể hối lại trong 1 khoảng thời gian". Loại vì (a) vẫn không giải quyết được mâu thuẫn ngữ nghĩa ở lý do #1 (dữ liệu chưa THẬT SỰ mất), và (b) vẫn cần sửa MỌI câu query đọc `memory_items` để lọc `deleted_at IS NULL` — chi phí triển khai gần bằng phương án Trash đầy đủ mà không có ưu điểm "xóa thật ngay" của phương án đã chọn.
3. **Cho người dùng CHỌN giữa 2 chế độ (`--soft`/`--hard`) mỗi lần gọi `forget`** — linh hoạt nhất, không phải quyết định 1 chiều. Loại vì thêm bề mặt quyết định cho một hành động vốn đã hiếm khi dùng (over-engineering, P4) — và một cờ mặc định sai (`--soft` mặc định) sẽ lặp lại đúng vấn đề đang cố giải quyết, trong khi mặc định `--hard` duy nhất thì cờ `--soft` trở thành thừa (đã có `paosctl memory update`/sửa confidence tay nếu chỉ muốn "hạ thấp" chứ không xóa hẳn).

---

## ADR-0030 — `psutil` cho Energy Engine đo CPU/RAM/pin thật; KHÔNG thêm thư viện GPU vendor-specific

**Trạng thái:** Accepted · 2026-08 · Quyết định P-M7-2 ([doc 19](19-prompt-library.md))

**Bối cảnh:** doc 06 §4 Energy Engine cần đọc trạng thái máy THẬT (CPU util, RAM, pin/AC, nhiệt độ, GPU util/VRAM) để quyết định "máy có đang bận không" trước khi cấp resource token — khác biệt so với chỉ đếm số Task nội bộ paosd đang giữ token (`asyncio.Semaphore`, đã có từ M2-3): máy có thể bận vì ứng dụng KHÁC ngoài paosd. Cần chọn công cụ đọc sensor hệ điều hành. Máy dev thật là Windows (10 Pro N Workstation) — công cụ phải hoạt động ở đó, không chỉ trên Linux/CI.

**Quyết định:** Dùng `psutil>=6.0` cho CPU (`cpu_percent()`), RAM (`virtual_memory()`), pin/AC (`sensors_battery()`) — cả 3 đã kiểm chạy thật trên Windows dev machine. **KHÔNG** thêm `pynvml`/`GPUtil`/`nvidia-ml-py` cho GPU ở lát này — GPU util/VRAM trong `policies/energy.yaml` khai báo nhưng chưa được `EnergyEngine` đọc, đánh dấu rõ "chưa đo được". Nhiệt độ (`psutil.sensors_temperatures()`) gọi được nhưng **raise `AttributeError` trên Windows** (chỉ implement cho Linux) — `EnergyEngine` bắt lỗi này, coi là "không đo được" (`None`), KHÔNG chặn vì thiếu dữ liệu, KHÔNG giả vờ có số.

**Lý do:**
1. **`psutil` đã kiểm thật trên đúng nền tảng dev** (không phải giả định tài liệu) — `cpu_percent()`/`sensors_battery()`/`virtual_memory()` đều trả giá trị hợp lệ trên Windows 10 lúc viết ADR này; `sensors_temperatures()` xác nhận KHÔNG khả dụng, ghi vào quyết định thay vì phát hiện muộn lúc chạy thật.
2. **GPU vendor-specific đụng POR (tính di động, doc 11)** — `pynvml` chỉ chạy được với GPU NVIDIA + driver cài đúng; máy không NVIDIA (AMD/Intel/Apple Silicon) sẽ crash hoặc luôn báo "không có GPU". Thêm phụ thuộc cứng cho 1 hãng phần cứng khi hệ thống còn đang chạy `stub`/`local` provider (chưa GPU nào thật sự cần theo dõi util% ngoài test) là đầu tư sớm không cần thiết (P4).
3. **`psutil` đã là lựa chọn "boring" đúng nghĩa P11** — thư viện thuần Python (kèm C extension biên dịch sẵn), 15+ năm, không phụ thuộc thêm service nền, không phải AI SDK (không đụng Cổng CI nào).
4. **Không giả vờ đo được cái chưa đo được tốt hơn đo sai.** `EnergyEngine.check()` chỉ chặn dựa trên tín hiệu THẬT SỰ đọc được — thiếu 1 sensor (nhiệt độ trên Windows, GPU luôn) không được phép làm hỏng toàn bộ quyết định lịch, chỉ đơn giản là số hạng đó vắng mặt.

**Hệ quả:** `policies/energy.yaml::gpu` và `::thermal` (trên Windows) là cấu hình khai báo nhưng KHÔNG có driver đọc — chờ ADR riêng nếu/khi cần GPU util thật (ví dụ khi có provider local dùng ComfyUI/GPU nặng thường trực, hoặc máy dev đổi sang Linux). `cpu_percent(interval=None)` cần "mồi" 1 lần lúc khởi động (`EnergyEngine.__init__`) — lần gọi đầu tiên có thể trả 0.0, chấp nhận được vì thiên về "cho phép" (an toàn theo hướng không chặn oan), không phải hướng nguy hiểm (chặn oan mới cần lo).

**Đã loại:**
1. **`pynvml` + `psutil` (đo cả GPU NVIDIA thật)** — đúng đủ 5 tín hiệu doc 06 §4 liệt kê. Loại vì lý do #2 (vendor lock-in phần cứng) và vì chưa có bằng chứng cần thật (chưa provider nào chạy GPU nặng thường trực ngoài test giả — RSK-04 "thêm Engine mới khi chưa có nhu cầu thật" áp dụng tương tự cho "thêm số hạng engine khi chưa có nhu cầu thật").
2. **`GPUtil`** (wrapper mỏng quanh `nvidia-smi`) — nhẹ hơn `pynvml` nhưng cùng vấn đề vendor lock-in, và dự án đã bảo trì yếu hơn `psutil` (ít commit gần đây, không phải "boring technology" đã kiểm chứng lâu dài như `psutil`).
3. **Không đo gì cả — chỉ dùng số Task nội bộ (`asyncio.Semaphore`) làm tín hiệu duy nhất**, bỏ hẳn khái niệm "máy bận vì ứng dụng khác". Loại vì đây chính xác là khoảng trống doc 06 §4 nêu tên ("theo dõi GPU util/VRAM, CPU load...") — semaphore nội bộ chỉ biết paosd tự cạnh tranh với chính nó, không biết gì về phần còn lại của máy người dùng đang dùng.

---

## ADR-0031 — Time Engine dùng giờ UTC hệ thống trực tiếp; Savings Report tính từ `estimate()` thật, không suy diễn giá cloud trung bình

**Trạng thái:** Accepted · 2026-08 · Quyết định P-M7-3 ([doc 19](19-prompt-library.md))

**Bối cảnh:** doc 06 §5 cần Time Engine trả lời "giờ này chạy capability nặng được không" theo `policies/time.yaml::windows` (tên, ngày trong tuần, khung giờ, `allow_heavy`/`allow`/`max_parallel`). doc 13 M7 exit criteria + doc 06 §3.4 cần thêm báo cáo tháng "đã tiêu bao nhiêu, tiết kiệm bao nhiêu nhờ local + cache". Cả 2 phần đều có những chỗ doc không quyết định sẵn, phải chốt ở lát này:
1. `kernel/clock.py::now()` trả UTC, không có cấu hình timezone local ở bất kỳ đâu trong hệ thống — trong khi `policies/time.yaml` viết giờ kiểu "08:00-18:00" như giờ địa phương.
2. `allow_heavy: false` cần biết capability nào là "nặng" — không có field nào trên `capability.yaml`/`provider.yaml` khai báo việc này.
3. `deadline` miễn trừ (doc 06 §5) cần `Router.call()` biết deadline của Job — nhưng `JobFeatures.deadline` (đã trích từ P-M6-1) chưa từng được truyền xuống Router.
4. "Tiết kiệm nhờ local/cache" cần một con số THẬT — nhưng repo hôm nay KHÔNG có provider cloud nào đăng ký (chỉ local/stub), nên không có "giá cloud" nào đo được để so sánh; `cache_entries` (migration 004) cũng chưa từng lưu `saved_cost`.

**Quyết định:**
- **Timezone:** `TimeEngine.check()` nhận `now: datetime` từ `clock.now()` (UTC) và so khớp TRỰC TIẾP với `policies/time.yaml`, không quy đổi — cùng cách `CostEngine.check_budget()` đã tính `start_of_day`/`start_of_month`. Vận hành viên tự viết giờ trong `time.yaml` theo đúng giờ hệ thống chạy (UTC), không có tầng dịch timezone.
- **"Nặng":** cùng khuôn `policies/energy.yaml::battery.on_battery.allow` — `policy.allow_heavy: false` chặn MỌI capability trừ những cái liệt kê trong `policy.allow` (allowlist tường minh cho từng cửa sổ), không suy đoán "nặng" từ tên/loại capability.
- **`max_parallel`:** đọc/parse nhưng KHÔNG enforce — `Runner` vẫn giữ đúng 1 `asyncio.Semaphore` toàn cục.
- **`deadline`:** KHÔNG làm ở lát này — `Router.call()` chưa nhận tham số `deadline`.
- **Savings — cơ chế chung cho cả "cache" và "local":** số tiền tránh được LUÔN là kết quả `adapter.estimate()` THẬT gọi trên MỘT provider CỤ THỂ đã đăng ký thật trong Registry (provider đã tạo ra kết quả đang cache, hoặc candidate cloud/hybrid xếp hạng cao nhất còn ĐỦ ĐIỀU KIỆN cho cùng lượt gọi) — tái dùng chính cơ chế `_check_budget()` đã dùng từ P-M7-1. Ghi vào bảng mới `savings_entries` (migration 011, `kind`="cache"|"local") qua `CostEngine.record_savings()`, tổng hợp bằng `CostEngine.monthly_report()`. Phát thêm 2 Event doc 05 đã đặt tên sẵn từ đầu: `time.window.blocked` (§3.8) và `capability.cache.hit` (§3.5, payload thêm `provider_id` so với doc gốc để trace được).
- **Currency:** `MonthlyReport` giả định 1 currency duy nhất cho cả báo cáo (mặc định "JPY", khớp mọi `provider.yaml::cost.currency` khai trong repo hôm nay).

**Lý do:**
1. **Không giả vờ có số** (cùng nguyên tắc ADR-0030) — nếu không có provider cloud/hybrid nào ĐĂNG KÝ THẬT cho 1 capability, "tiết kiệm nhờ local" cho lượt gọi đó đúng là KHÔNG đo được, không phải bằng 0 giả hay một hằng số "giá cloud trung bình" bịa ra. Repo hôm nay chỉ có local/stub provider → `saved_local` sẽ luôn là 0 trong thực tế — đây là phản ánh đúng trạng thái hệ thống, không phải lỗi thiếu sót của Savings Report.
2. **Tái dùng `estimate()` đã được tin cậy** thay vì viết một công thức tính giá song song — `_check_budget()` (P-M7-1) đã coi `adapter.estimate()` là nguồn sự thật duy nhất cho "cuộc gọi này giá bao nhiêu"; Savings Report chỉ gọi lại đúng hàm đó trên provider bị tránh, không phát minh thêm 1 cách tính giá khác có thể trôi khỏi cách CostEngine đã tính.
3. **UTC trực tiếp, không dịch timezone** — thêm tầng cấu hình timezone cho ĐÚNG 1 file `policies/time.yaml` là đầu tư sớm không cần thiết (P4) khi chưa có ca dùng thật nào cần chạy PAOS lệch múi giờ với máy host; cùng tinh thần CostEngine đã chấp nhận UTC cho ranh giới ngày/tháng từ P-M7-1 mà không ai coi là thiếu.
4. **Allowlist thay vì suy đoán "nặng"** — cùng lý do Energy Engine đã chọn allowlist cho `battery.on_battery.allow` thay vì đoán theo tên capability: một danh sách tường minh trong YAML luôn đúng ý người cấu hình, một quy tắc suy đoán (vd theo capability_id chứa "render"/"video") sẽ có ngoại lệ không lường trước được ngay từ ví dụ tiếp theo.

**Hệ quả:** `saved_local` sẽ hiển thị 0₫ cho tới khi có provider cloud/hybrid THẬT đăng ký — không phải bug, xem docs/backlog.md cho điều kiện trả nợ `deadline`/`max_parallel`/timezone. `paosctl report` gọi `GET /v1/reports/monthly` — CostEngine dựng riêng 1 instance trong `apps/paosd/app.py::create_app()` (không dùng chung instance của Router, cùng tiền lệ CacheStore).

**Đã loại:**
1. **Suy diễn "giá cloud trung bình" từ lịch sử `cost_entries` khi không có candidate cloud đủ điều kiện** (vd trung bình `amount` mọi lần capability này từng chạy cloud trong quá khứ) — loại vì đây CHÍNH LÀ "giả vờ có số" mà ADR-0030 đã từ chối cho sensor thiếu: nếu không có candidate cloud ĐANG đủ điều kiện ngay lúc quyết định, một con số lịch sử (có thể đã lỗi thời, giá đã đổi, provider đã gỡ) sẽ đánh lừa người đọc báo cáo rằng hệ thống "biết" một mức giá không còn thật.
2. **Thêm cấu hình timezone local cho `policies/time.yaml`** (vd trường `tz: Asia/Tokyo`) — loại vì chưa 2 ca dùng thật (P4), và thêm 1 field cấu hình mới kéo theo phải sửa `_time_in_range()`/mọi chỗ so khớp giờ, cho một nhu cầu chưa ai gặp phải (máy host của PAOS và giờ người dùng muốn áp dụng chính sách luôn là CÙNG múi giờ trong mọi ca dùng thật tới nay).
3. **Luồng `deadline` đầy đủ** (`JobSpec.constraints.deadline` → `Process` → `Router.call(deadline=...)` → `TimeEngine` miễn trừ + ghi Decision Record giải thích) — loại khỏi phạm vi lát này vì đụng tới nhiều tầng (`ProcessManager.create()` là hợp đồng dài hạn M1, không sửa tùy tiện) cho một nhánh hành vi chưa có ca dùng thật nào chờ sẵn; `JobFeatures.deadline` đã trích từ P-M6-1 nhưng chưa từng có tiêu dùng thật — thêm 1 tiêu dùng nữa (Time Engine) mà không giải quyết luôn cả đường ống sẽ để lại nửa vời.
4. **Enforce `max_parallel` theo cửa sổ đang hoạt động** (đổi `Runner._process_slots` động theo `TimeEngine`) — loại vì `Runner` hôm nay dùng đúng 1 `asyncio.Semaphore` cố định từ lúc khởi tạo; đổi capacity một Semaphore đang chạy giữa chừng an toàn (không làm rò token) cần thiết kế riêng, ngoài phạm vi "cửa sổ thời gian chặn/cho phép 1 lượt gọi" của lát này.

---

## Backlog ADR (chưa quyết định, cần trước milestone tương ứng)

| Dự kiến | Chủ đề | Cần trước |
|---|---|---|
| ADR-0019 | Chiến lược đồng bộ nhiều máy | v2 |
| ADR-0020 | Chữ ký số cho plugin | v2 |

**ADR-0015/0016 đã chốt (P-M5-0, 2026-08-16)** — xem đầy đủ ở trên, đúng lúc cần trước M5.

**ADR-0017/0018 đã chốt (P-M8-0, 2026-08-16)** — xem đầy đủ ở trên, đúng lúc cần trước M8.

**Về thứ tự số:** ADR-0015→0020 được đánh số trước (đặt chỗ khi backlog được nhận diện) nhưng quyết định sau, đúng lúc milestone cần. ADR-0021→0024 lại được **quyết định sớm hơn** — chúng là các quyết định kỹ thuật bắt buộc phải chốt ngay ở Ngày 0 để M0 có nền để đứng ([doc 18 §3](18-day0-implementation-playbook.md)), nên số ADR không đơn điệu theo thời gian chốt. Số ADR chỉ là định danh duy nhất, không phải thứ tự thời gian — đọc **Trạng thái** và ngày để biết cái nào đã Accepted.
