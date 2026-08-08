# 18 — Ngày 0 & Playbook Triển khai

**Trạng thái:** Draft v0.1 · **Loại:** công cụ vận hành, không phải hợp đồng — nếu mâu thuẫn với doc 00–17, các doc kia thắng (cùng nguyên tắc với doc 19 §0)

> **Ghi chú xuất xứ:** tài liệu này được dựng lại (2026-08) sau khi phát hiện nó bị tham chiếu khắp `docs/`, `.gitignore`, các script CI và toàn bộ doc 19 mà chưa từng tồn tại — một lỗ hổng đối xứng nghiêm trọng hơn là thẩm mỹ, vì doc 19 giả định nội dung của nó đã có sẵn ở gần như mọi prompt Ngày 0 và M0. Nội dung dưới đây được suy ra chặt chẽ từ ~40 điểm trích dẫn cụ thể (số mục, chữ cái nhóm, tên biến) rải rác trong dự án, cộng với kiến trúc đã chốt ở doc 00–17. Đây là bản dựng lại có căn cứ, **không phải trí nhớ của người viết dự án** — đọc lại và sửa trước khi coi là chốt.

---

## §1 · Mục đích & cách dùng

Doc 00–17 nói **PAOS phải là gì**. Doc 19 nói **dùng trợ lý AI để viết nó theo thứ tự nào**. Tài liệu này là lớp ở giữa: **cách chuẩn bị máy, khung repo, danh mục rủi ro triển khai cụ thể, và kịch bản nghiệm thu** — thứ mà doc 19 giả định đã có sẵn ở mọi prompt Ngày 0 và M0.

Ba đối tượng đọc doc này:
1. Bạn, trước khi gõ dòng code đầu tiên (§2–§4).
2. Bạn, trong lúc làm M0 (§5–§6, song song với doc 19 §3–§4).
3. Trợ lý AI, khi bạn dán một prompt từ doc 19 có nhắc "doc 18 §X" — nó cần đọc được đúng phần đó.

---

## §2 · Kiểm môi trường

### 2.1 Mục đích

Trước khi tạo repo, xác nhận máy có thể chạy PAOS ở chế độ tối thiểu. Bỏ qua bước này là nguồn phổ biến nhất của "code đúng nhưng không chạy được" trong tuần đầu.

### 2.2 Danh sách kiểm E1–E9

| # | Kiểm | Lệnh gợi ý | Bắt buộc cho M0? |
|---|---|---|---|
| E1 | Python ≥ 3.12 | `python3 --version` | Có |
| E2 | SQLite ≥ 3.37 (WAL + `RETURNING`, dùng ở ADR-0023) | `python3 -c "import sqlite3; print(sqlite3.sqlite_version)"` rồi `PRAGMA journal_mode=WAL;` | Có — cũng là kiểm bắt buộc trên CI runner, cùng ngưỡng chính xác ([.github/workflows/ci.yml](../.github/workflows/ci.yml) job `test`) |
| E3 | Đĩa trống nơi đặt `workspace/` | ≥ 20GB | Có |
| E4 | `ffmpeg` trong PATH | `ffmpeg -version` | Không (cần trước M3) |
| E5 | Ollama cài đặt và trả lời | `curl localhost:11434/api/tags` | Không — M0 chạy đủ với `providers/stub/` nếu thiếu |
| E6 | GPU/VRAM khả dụng | `nvidia-smi` (nếu có) | Không — ảnh hưởng LOC-05, không chặn M0 |
| E7 | Git đã cấu hình | `git --version` + `user.name`/`user.email` | Có |
| E8 | Mạng ra ngoài khả dụng | ping một endpoint cloud provider dự kiến dùng | Không — chỉ cần trước khi test nhánh fallback cloud |
| E9 | `workspace/` **không** nằm trong thư mục đồng bộ cloud (OneDrive/Dropbox/Google Drive) | kiểm đường dẫn tay | Có — xem §2.3 |

**Đạt nhưng đáng lo:** nếu E5/E6 fail, M0 vẫn làm được đầy đủ bằng `providers/stub/` (xem §5, lát 4b) — ghi nhận là nợ, không phải chặn. Nếu E9 fail, đó là rủi ro thật (RSK-08 kiểu mới): công cụ đồng bộ cloud có thể khóa file `state.db` giữa lúc SQLite đang ghi WAL, gây lỗi khó tái lập. Di chuyển `workspace/` ra ngoài thư mục sync trước khi tiếp tục.

### 2.3 Quy tắc git: `workspace/` không bao giờ được commit

**Quan trọng nhất trong toàn bộ §2.** `workspace/` chứa model (hàng GB), artifact cá nhân, và `state.db` — dữ liệu người dùng thật, không phải code. Commit nhầm một lần là hỏng lịch sử git vĩnh viễn (phải rewrite history hoặc bỏ repo).

Trước khi `git init`:
1. Xác nhận `.gitignore` có `workspace/`, `*.db`, `*.db-wal`, `*.db-shm`, `.paos/` (đã có sẵn trong `docs/.gitignore` — copy nguyên vào gốc repo, không viết lại tay).
2. Không bao giờ `git add -A` trong `workspace/` hoặc ở gốc repo nếu chưa kiểm `git status` trước — đây là quy tắc chung của toàn bộ dự án, nhưng đặc biệt nguy hiểm ở đây vì `workspace/` có thể chứa file nặng hàng GB không hiện rõ trong terminal.
3. `paosctl` không bao giờ tạo file ngoài `workspace/` hoặc `docs/` — kiểm điều này bằng test sandbox fs ngay từ M0.

---

## §3 · Mười hai quyết định Ngày 0

Mười hai quyết định kỹ thuật nhỏ nhưng phải chốt **trước** dòng code đầu tiên — không phải vì chúng quan trọng bằng 4 hợp đồng dài hạn (doc 04), mà vì đổi ý giữa chừng tốn thời gian hơn nhiều so với chọn sai lúc đầu rồi sống với nó.

| # | Quyết định | Chốt |
|---|---|---|
| D-01 | Quản lý dependency | `pyproject.toml` chuẩn PEP 621 + `pip install -e ".[dev]"` (đã phản ánh trong [Makefile](../Makefile) `install`, chạy được từ gốc repo). Không Poetry/PDM — một công cụ ít hơn để bảo trì (P11). |
| D-02 | Clock & ID | `kernel/clock.py` là **nguồn duy nhất** của thời gian (không gọi `datetime.now()` ở nơi khác — cấm bằng grep test); `kernel/ids.py` sinh ULID có tiền tố qua thư viện có monotonic guard (ADR-0023). Cả hai injectable để test tất định (`PAOS_MODE=deterministic`). |
| D-03 | Test framework | `pytest` + `pytest-asyncio`; marker tùy chỉnh `requires_ollama`, `requires_gpu`, `slow`, `chaos` (đã dùng trong `Makefile` `test`/`test-all`). |
| D-04 | Định dạng log | JSON dòng (ndjson) qua `logging` chuẩn + formatter tùy chỉnh; không thêm thư viện log ngoài ở M0 (doc 10 §5). |
| D-05 | Định dạng cấu hình | YAML cho mọi policy/config người dùng sửa tay; JSON Schema cho hợp đồng máy-máy (ADR-0022); không trộn thêm TOML ngoài `pyproject.toml`. |
| D-06 | Phiên bản Python cụ thể | Ghim `>=3.12,<3.13` ở M0 để tránh lệch hành vi giữa các bản vá; mở rộng ma trận CI khi có lý do thật. |
| D-07 | Thư viện CLI cho `paosctl` | `click` — ổn định lâu năm, ít phụ thuộc, đúng tinh thần P11 hơn các framework CLI mới. |
| D-08 | Cấu trúc thư mục test | `tests/` song song ở gốc repo, phản chiếu cấu trúc package (`tests/kernel/`, `tests/contract/`...), không nằm lồng trong `kernel/`. |
| D-09 | Cưỡng chế Conventional Commits | Chỉ quy ước ở M0 (doc 17 §5), chưa gắn commit-msg hook — xét lại nếu vi phạm lặp lại sau M1. |
| D-10 | Driver SQLite | `aiosqlite` cho single-writer actor (ADR-0024) — khớp mô hình asyncio của Kernel (ADR-0001) mà không cần tự quản executor riêng. |
| D-11 | Cơ chế migration | Script Python đánh số trong `kernel/state/migrations/`, tự viết `up`/`down`, không dùng Alembic — schema đủ đơn giản để không cần công cụ ngoài (P11), và migration là hợp đồng dữ liệu (doc 03) nên cần kiểm soát tuyệt đối nội dung SQL chạy. |
| D-12 | Đóng gói/phân phối `paosd` | **Chưa chốt — hoãn có điều kiện tới M8** cùng ADR-0017/0018. Ngày 0 không cần quyết định này; đừng để nó chặn tiến độ. |

**Kiểm tra cuối Ngày 0:** rà lại cả 12 dòng trên, hỏi với mỗi dòng: "đây là quyết định tôi thật sự đã chốt, hay chỉ đang 'tạm chấp nhận' vì chưa nghĩ kỹ?" (đúng câu hỏi P-D0-3 dùng, doc 19 §3).

---

## §4 · Khung repo

### 4.1 Cây thư mục Ngày 0

```
paos/
├── kernel/
│   ├── __init__.py
│   ├── errors.py            # PaosError + 13 mã chuẩn (doc 04 §1)
│   ├── ids.py                # ADR-0023
│   ├── clock.py              # D-02
│   ├── state/
│   │   ├── db.py             # single-writer actor, ADR-0024
│   │   └── migrations/
│   │       └── 001_init.sql
│   ├── events/
│   │   ├── bus.py
│   │   └── types.py          # EventType (StrEnum)
│   ├── process/
│   ├── scheduler/
│   └── registry/
├── sdk/                       # doc 12 §3 — mặt cho người viết Agent/Plugin
├── capabilities/               # *.yaml — doc 04 §2.1
├── providers/
│   ├── stub/                  # D-lát 4b — tất định, dùng suốt dự án
│   └── ollama/                 # D-lát 4c — adapter ≤ 200 dòng
├── agents/
│   └── summarize/               # agent đầu tiên, đủ 6 bước vòng đời
├── apps/
│   └── paosd/
│       └── routes/             # TOÀN BỘ mã HTTP sống ở đây, ADR-0021
├── plugins/                     # trống ở M0 — chỉ tồn tại để CI gate 3 test "xóa thư mục này Kernel vẫn chạy"
├── schemas/
│   └── events/                  # *.schema.json — doc 05
├── policies/                    # routing.yaml, budget.yaml... — trống/mẫu ở M0
├── scripts/                      # ci-kernel-isolation.sh, check-event-schemas.py, check-docs-sync.sh
├── tests/
│   ├── kernel/
│   │   └── _fakes.py          # provider/agent GIẢ để test Kernel — cổng 2 (ci-kernel-isolation.sh)
│   ├── contract/
│   ├── golden/
│   ├── eval/
│   └── security/
├── docs/                         # toàn bộ 00–20
├── workspace/                     # KHÔNG commit — §2.3
├── pyproject.toml
├── Makefile
├── .importlinter
├── .gitignore
└── .github/workflows/ci.yml
```

Mỗi package (`kernel/`, `sdk/`, `capabilities/`, `providers/`, `agents/`, `apps/`) có `__init__.py` kèm một dòng docstring nói nó chịu trách nhiệm gì — đây là tài liệu sống đầu tiên của repo, không phải thủ tục hình thức.

### 4.2 File tối thiểu để 4 cổng CI xanh trên repo gần rỗng

| File | Vì sao cần ngay |
|---|---|
| `kernel/events/types.py` — `class EventType(StrEnum)` | bắt đầu bằng `kernel.startup`, `kernel.shutdown` — để gate 5 có gì đó để kiểm |
| `schemas/events/kernel.startup.schema.json`, `kernel.shutdown.schema.json` | gate 5 (`check-event-schemas.py`) cần ít nhất 1 cặp event/schema khớp nhau |
| `tests/security/test_redaction.py` + hàm `redact()` tối giản (3 mẫu: `sk-`, `Bearer `, `key=`) | gate 4 cần có gì để chạy — mở rộng thật ở M2 (P-M2-5) |
| `tests/kernel/test_smoke.py` | gate 2 (`rm -rf providers/ agents/ plugins/ && pytest tests/kernel/`) cần ít nhất 1 test không rỗng |

### 4.3 Xác nhận từng cổng trước khi viết gì thêm

Chạy `make gates` ngay sau khi 4 file trên tồn tại. Nếu một cổng đỏ ở đây, đừng viết thêm code — sửa cổng hoặc sửa hiểu biết về cổng trước.

### 4.4 Chính sách khoan dung của các cổng CI ở giai đoạn đầu

Hai cổng có vùng xám cần nói rõ ngay từ Ngày 0, để không có ai (kể cả trợ lý AI) tự ý "linh hoạt hóa" chúng giữa chừng:

**a) Cổng 1 (Kernel sạch AI) — chi tiết dễ hiểu lầm.** `make gate1` grep **cả comment và docstring**, không chỉ code thực thi:

```bash
grep -rniE "openai|anthropic|gpt|claude|ollama|comfyui|llm|prompt" kernel/ --include="*.py"
```

Hệ quả thực tế: một docstring giải thích "hàm này KHÔNG được gọi OpenAI" cũng tự kích hoạt gate — vì chuỗi `openai` xuất hiện, bất kể ngữ cảnh phủ định. Đừng viết lời giải thích chứa từ cấm trong `kernel/`; nếu cần giải thích ranh giới, dùng từ thay thế ("provider bên ngoài", "dịch vụ mô hình") hoặc đặt lời giải thích ở `docs/`.

Ngoại lệ đã biết gây báo giả: biến `model_config` của Pydantic khớp pattern `\bllm\b`? Không — nhưng `model=` (dùng để kiểm P3 ở `agents/`, không phải P1 ở `kernel/`) từng gây nhầm giữa hai cổng khi copy-paste rule. Giữ 2 danh sách từ khóa của gate1 (P1, `kernel/`) và gate3 (P3, `agents/`) tách biệt rõ trong `Makefile`, không dùng chung một biến. Đây là nguồn gốc của R29 (§6).

**b) Cổng 6 (docs đồng bộ, MNT-09) — vì sao chỉ cảnh báo, chưa chặn.** `scripts/check-docs-sync.sh` cố ý `exit 0` ngay cả khi phát hiện PR chạm hợp đồng dài hạn mà không sửa `docs/` — nó in cảnh báo rồi vẫn cho qua. Đây không phải sơ suất: chặn cứng cổng này *trước khi* 4 hợp đồng dài hạn (doc 04) đã ổn định qua thực chiến (tức trước khi đóng M2) sẽ tạo ra áp lực sai — buộc sửa doc cho mọi thử nghiệm nhỏ ở M0/M1, khi bản thân hợp đồng còn có thể đổi hình dạng vài lần. Mốc nâng lên chặn (`exit 1`) là **sau khi đóng M2** — ghi lại ở đây để không quên, và vì đây chính là câu hỏi P-QUARTER hỏi lại mỗi quý ("có cổng nào đã được nới lỏng lúc nào đó mà tôi quên không?"): cổng 6 không phải bị nới lỏng, nó được **thiết kế** để bắt đầu lỏng rồi siết lại đúng lịch — khác nhau ở việc có ghi ngày hẹn rõ ràng hay không.

### 4.5 Checklist "Xong" của Ngày 0

- [ ] E1, E2, E3, E7, E9 ở §2.2 đạt (E4–E6, E8 ghi nhận trạng thái, không chặn)
- [ ] `workspace/` xác nhận nằm ngoài thư mục đồng bộ cloud
- [ ] Cây thư mục §4.1 tồn tại đầy đủ, mỗi package có docstring
- [ ] `make gates` chạy — cả 6 cổng có kết quả (xanh hoặc đỏ có lý do đã hiểu, không có cổng "chưa chạy được")
- [ ] `.gitignore` gốc repo khớp `docs/.gitignore`, đã kiểm bằng `git status` rằng không có gì nặng bị track
- [ ] Mười hai quyết định ở §3 đã rà, không còn mục "tạm chấp nhận"
- [ ] Bốn ADR 0021–0024 đã ghi vào doc 15
- [ ] Commit đầu tiên: `main` ở trạng thái chạy được (dù chỉ là "chạy được" = `make gates` không crash)

---

## §5 · M0 — Năm lát cắt (tổng quan)

Chi tiết từng lát cắt, ràng buộc kỹ thuật và test bắt buộc đã có đầy đủ ở [doc 19 §4](19-prompt-library.md) (prompt P-M0-1 → P-M0-6). Bảng dưới đây chỉ là bản đồ nhanh — **không lặp lại nội dung**, chỉ neo lát cắt vào rủi ro và hợp đồng liên quan.

| Lát | Tên | Hợp đồng chạm | Nhóm rủi ro liên quan (§6) |
|---|---|---|---|
| 1 | State Store | doc 03 §3 (schema), ADR-0023, ADR-0024 | Nhóm A (R01–R07) |
| 2 | Event Bus | doc 05 §1 (envelope), ADR-0003 | Nhóm B (R08–R12) |
| 3 | Process state machine | doc 03 §2.2, ADR-0021, ADR-0023 | Nhóm C (R13–R15) |
| 4 | Capability & 2 Provider | doc 04 §2, ADR-0004, ADR-0009 | Nhóm C (R18–R22) |
| 5 | Agent, CLI, Trace | doc 04 §3, doc 12 §3 | Nhóm C (R18, R22), Nhóm D (R29 nếu chạm CI) |

Nguyên tắc xuyên suốt cả 5 lát (nhắc lại vì hay bị quên khi vội): **mỗi lát chạy `P-CONTRACT` trước, dừng chờ duyệt thiết kế, rồi mới `P-IMPL`.** Gộp thiết kế và code trong một hơi là cách phổ biến nhất khiến M0 kéo dài gấp đôi ước lượng.

---

## §6 · Danh mục rủi ro triển khai (R01–R38)

Khác với [doc 14](14-risk-register.md) (rủi ro **chiến lược** của cả dự án — over-engineering, kiệt sức, scope creep), danh mục này là rủi ro **kỹ thuật cụ thể** phát hiện được ở mức thiết kế/code, gắn với từng lát cắt. Bốn nhóm theo tầng kiến trúc chạm tới, không theo mức độ nghiêm trọng.

### Nhóm A — Nền tảng dữ liệu & khởi động (R01–R07) — lát 1

| # | Rủi ro | Giảm thiểu |
|---|---|---|
| R01 | Hai tiến trình `paosd` cùng chạy migration lúc khởi động → race hỏng schema | File lock/PID lock trước khi migration bắt đầu |
| R02 | Backup trước migration lỗi bị nuốt (`except: pass`) → migration chạy tiếp không có đường lùi | Backup thất bại = dừng migration ngay, không tiếp tục |
| R03 | `PRAGMA integrity_check` chỉ chạy khi gọi tay `doctor` → DB hỏng âm thầm nhiều tuần | Chạy check nhẹ mỗi lần khởi động, full check theo lịch |
| R04 | Bảng mới thêm sau này thiếu `schema_version` từ đầu | Mọi `CREATE TABLE` mới bắt buộc có cột này ngay từ commit đầu tiên |
| R05 | Gọi `datetime.now()` trực tiếp ngoài `kernel/clock.py` → test không tất định | Grep test cấm import `datetime.now` ngoài file được phép |
| R06 | ULID không đơn điệu khi đồng hồ hệ thống lùi lại (NTP resync) | Thư viện ULID có monotonic guard trong cùng millisecond |
| R07 | Đọc-sửa-ghi `counters` không atomic ở nơi khác ngoài `db.py` → trùng PID | Chỉ `kernel/state/db.py` được cấp PID, luôn qua `UPDATE...RETURNING` trong transaction |

### Nhóm B — Event Bus & concurrency (R08–R12) — lát 2

| # | Rủi ro | Giảm thiểu |
|---|---|---|
| R08 | Exception ở dispatch loop thoát ra ngoài, giết tiến trình, event kẹt "chưa dispatch" mãi | Top-level try/except quanh dispatch, lỗi bọc riêng theo subscriber |
| R09 | Subscriber đăng ký sau vẫn cần đọc event cũ nhưng thiếu bước "catch-up scan" | Subscriber mới quét theo con trỏ trước khi vào vòng lặp live |
| R10 | Payload > 32KB ghi thẳng vào `events.payload_json` → phình DB (SCL-05) | Cưỡng chế giới hạn ở `publish()`, tự chuyển thành Artifact + `artifact_id` |
| R11 | `correlation_id`/`causation_id` để trống "vì M0 chưa cần" → không backfill được chính xác sau này | Envelope đủ 10 trường từ M0 dù logic dùng chúng đến sau (nguyên tắc §10) |
| R12 | `ProjectLogger` ghi `events.ndjson` không atomic → file hỏng khi crash giữa dòng | Flush tường minh sau mỗi dòng, append-only |

### Nhóm C — Vòng đời Process, Capability & Agent (R13–R22) — lát 3–5

| # | Rủi ro | Giảm thiểu |
|---|---|---|
| R13 | `CancelledError` bị nuốt bởi `except Exception` chung khi hủy Process | Bắt riêng, luôn re-raise, không log như lỗi thường |
| R14 | Bảng chuyển trạng thái thiếu entry mặc định → lỗi âm thầm thay vì `CONFLICT` | Transition không có trong bảng phải raise `CONFLICT` tường minh |
| R15 | Cancel đúng lúc đang ghi checkpoint → transaction dở dang, resume đọc hỏng | Cờ hủy chỉ kiểm tra ở điểm an toàn giữa các transaction |
| R16 | `apps/paosd/` import thẳng nội bộ Kernel thay vì API công khai → ADR-0021 xói mòn dần | Kernel expose `__all__` tường minh, checklist review riêng |
| R17 | API đọc trạng thái trực tiếp bộ nhớ tiến trình "cho nhanh" thay vì dựng từ event log | Test bắt buộc: restart rồi `explain` vẫn đầy đủ |
| R18 | Agent ghi artifact với path do input điều khiển chứa `../` → ghi ra ngoài Project | Resolve tuyệt đối + kiểm nằm trong `projects/<x>/`, test path traversal riêng |
| R19 | Lần gọi đầu Ollama nạp model (30–60s) bị tính nhầm là "provider treo" | Health-check timeout ngắn (3s) tách biệt invoke timeout dài (180s) |
| R20 | Bật `stream: true` quá sớm khi chưa có mô hình cho partial result | `stream: false` cưỡng chế ở M0 |
| R21 | Test quên reset `PAOS_STUB_FAIL` giữa các test → test sau bị nhiễm | Fixture pytest tự reset biến môi trường sau mỗi test |
| R22 | Agent nhận `provider_id` qua field phụ (vd `meta.provider_hint`) rồi log/dùng nó — vi phạm P3 "hợp pháp" | Lọc field provider khỏi `meta` trước khi tới AgentContext; CI mở rộng grep sang log của `agents/` |

### Nhóm D — CI/tooling & rủi ro xa hơn M0 (R23–R38)

| # | Rủi ro | Giảm thiểu |
|---|---|---|
| R23 | Cache CI của import-linter dùng graph cũ → vi phạm layer lọt nhiều PR | Không cache kết quả graph, cài lint-imports mới mỗi lần |
| R24 | Conformance suite chỉ chạy cho provider có sẵn lúc viết code, quên khi thêm provider mới | CI tự discover mọi provider đăng ký, không liệt kê tay |
| R25 | `max_parallel`/resource token hardcode "tạm cho nhanh" ở M1 | Checklist review đối chiếu MNT-08 mỗi PR chạm Scheduler |
| R26 | Rubric cho phép `fail_fast` không thực sự tiết kiệm (vẫn gọi LLM) | Test đo số lời gọi LLM cho artifact biết trước sẽ fail fast, phải = 0 |
| R27 | EWMA provider ranking khởi tạo `n=0` khiến provider mới luôn xếp cuối vĩnh viễn | `quality_hint` trong manifest làm giá trị khởi tạo, không phải 0 |
| R28 | Budget kiểm `per_job` mà quên cộng dồn `per_day` → vượt ngân sách tháng không ai biết | Mọi capability call kiểm cả 3 tầng, không chỉ tầng gần nhất |
| R29 | Gate1 grep cả comment/docstring gây báo giả, hoặc quá lỏng bỏ lọt | Grep không có vùng loại trừ theo ngữ cảnh; danh sách false-positive đã biết ghi trong Makefile (§4.4) |
| R30 | Plugin loader tin `paos_api` range khai báo mà không kiểm semver thật | Kiểm compatibility thật lúc discover, từ chối kèm hint nâng cấp |
| R31 | Sandbox network chỉ "khai báo" allow-list, không enforce ở tầng OS | Proxy nội bộ/network namespace chặn thật theo allow-list |
| R32 | Export/import workspace lỗi im lặng với đường dẫn dài Windows hoặc ký tự đặc biệt | Test trên cả 2 hệ điều hành mục tiêu, verify checksum sau import |
| R33 | Consolidation job hardcode giờ chạy, tranh tài nguyên đúng lúc cần máy nhất | Tự hoãn nếu Energy Engine báo máy đang bận |
| R34 | KG extractor tạo node trùng vì so khớp chuỗi thô (case, dấu tiếng Việt) | Chuẩn hóa trước khi so khớp, giữ bản gốc để hiển thị |
| R35 | `doctor` báo OK vì HTTP 200 mà không kiểm nội dung response đúng schema | `health()` validate cấu trúc tối thiểu, không chỉ status code |
| R36 | Fixture golden test commit kèm dữ liệu cá nhân thật chưa rà soát | Review riêng cho mọi fixture mới, quét bằng redaction trước khi commit |
| R37 | Chaos test "kill giữa chừng" chỉ xác minh trên Linux/macOS, không trên Windows (nơi dự án phát triển) | Kịch bản chaos riêng cho Windows (`taskkill /F`) |
| R38 | Gate 6 (MNT-09) chỉ cảnh báo, dễ bị phớt lờ dần tới khi tài liệu hết đáng tin | Mốc "nâng lên chặn sau M2" đã ghi trong `check-docs-sync.sh` — nhắc lại ở P-QUARTER mỗi quý |

---

## §7 · Kịch bản nghiệm thu M0

### 7.1 Bốn tiêu chí ở doc 13 + ba mục bổ sung

Doc 13 đã định nghĩa 4 exit criteria cho M0. Ba mục dưới đây **bổ sung**, không thay thế:

| # | Tiêu chí | Cách kiểm |
|---|---|---|
| 5 | `PAOS_MODE=deterministic` với `providers/stub/` cho cùng kết quả cấu trúc sau 20 lần chạy liên tiếp | Script chạy 20 lần, so sánh `workflow_trace.json` |
| 6 | `make gates` (cả 6 cổng, không chỉ gate1) xanh trên repo M0 | CI + chạy tay 1 lần trước khi coi M0 xong |
| 7 | `paosctl explain` sau khi **restart** `paosd` đọc hoàn toàn từ event log, không từ bộ nhớ tiến trình | Test tự động: chạy job → kill → start lại → explain, so sánh với explain lúc process còn sống |

### 7.2 Ma trận truy vết (traceability matrix)

| Tiêu chí | Nguồn | Test tương ứng |
|---|---|---|
| `paosctl run` → artifact → `explain` hiện trace | doc 13 M0 #1 | `tests/kernel/test_smoke.py`, mở rộng thành golden test tối giản |
| Event ghi DB trước dispatch | doc 13 M0 #2, REL-01 | test đơn vị `kernel/events/bus.py`: mock dispatch raise, kiểm event vẫn nằm trong DB |
| CI grep: 0 import AI trong Kernel | doc 13 M0 #3, MNT-01 | `make gate1` |
| Kill giữa chừng → resume không hỏng DB | doc 13 M0 #4, REL-02 | `scripts/ci-kernel-isolation.sh` + kịch bản chaos thủ công (SIGKILL/`taskkill /F` theo §6 R37) |
| Deterministic 20 lần | §7.1 #5 | `tests/apps/paosd/test_determinism.py` — vị trí khác dự kiến, xem §7.3 |
| 6 cổng CI xanh | §7.1 #6 | `make gates` trong CI |
| `explain` sống sót qua restart | §7.1 #7 | `tests/apps/paosd/test_explain_restart.py` + chaos thủ công (`taskkill /F`) |

Nếu bất kỳ hàng nào trong bảng chưa có test tương ứng, M0 **chưa nghiệm thu được** dù cảm giác "đã xong" — đây chính là câu hỏi P-M0-6 mục 3 ("có bước nào tôi đã sửa tay một chút mới chạy được không?"). **Cập nhật sau nghiệm thu (P-M0-6, 2026-08-08):** cả 7 hàng đã có test — hàng "Deterministic 20 lần" là hàng cuối cùng còn thiếu lúc bắt đầu nghiệm thu, đã lấp bằng `tests/apps/paosd/test_determinism.py`. M0 **đạt**.

### 7.3 Hậu kiểm M0 — kế hoạch đã sai ở đâu

Ba điểm playbook này đánh giá thấp hoặc bỏ sót, phát hiện lúc nghiệm thu (P-M0-6):

1. **Vị trí test determinism sai tầng.** §7.2 gợi ý `tests/kernel/test_determinism.py`, nhưng bài kiểm cần chạy đủ Process + Agent + Provider (qua `apps/paosd/wiring`), không phải Kernel đơn thuần — gate2 (§6 R... , `scripts/ci-kernel-isolation.sh`) xoá `apps/`/`agents/`/`providers/` trước khi chạy `tests/kernel/`, nên đặt ở vị trí gợi ý ban đầu sẽ vỡ ngay lập tức. Bài học: một tiêu chí nói "chạy end-to-end" gần như chắc chắn cần tầng `apps/`, không riêng Kernel — nên đặt tên thư mục test theo phạm vi thật của kịch bản, không theo tầng "nghe có vẻ đúng".

2. **"workflow_trace.json" là một khái niệm chưa từng thành hiện thực.** §7.1 #5 nhắc tới việc so sánh file `workflow_trace.json` sau 20 lần chạy — khái niệm này chưa từng được quyết định lúc viết playbook (Ngày 0), và thực tế hoá thành `paosctl explain` (HTTP) + `EventBus.events_for_process()`, không phải một file JSON riêng. Bài học: playbook viết ở Ngày 0 đôi khi cụ thể hoá sớm một chi tiết implementation chưa quyết — chấp nhận được nếu không khoá cứng thiết kế sau này vào chi tiết đó, nhưng nên đánh dấu rõ "tên tạm" thay vì viết như thể đã chốt.

3. **"Runner" — tầng kết nối Process ↔ Agent — chưa từng được đặt tên.** §5 (M0 — Năm lát cắt) và doc 19 P-M0-5 giả định `ProcessManager.create()` (lát 3) cộng với Agent Protocol (lát 5a/5b) là đủ để "chạy" một job. Thực tế cần thêm một tầng chủ động lắng nghe `kernel.process.created` rồi tự QUEUED → RUNNING → gọi Agent → SUCCEEDED/FAILED (`apps/paosd/runner.py`, lát 5c) — playbook không hề đặt tên hay lên kế hoạch cho tầng này. Đây là gap kế hoạch thật, không phải tiểu tiết: nếu không phát hiện lúc viết P-CONTRACT cho lát 5c, `POST /v1/jobs` sẽ mãi chỉ tạo Process ở `CREATED` rồi dừng.

   Hệ quả cho M1: Runner M0 hiện chạy Agent **đồng bộ** ngay trong `dispatch()` — `POST /v1/jobs` block tới khi agent chạy xong (chấp nhận được vì `StubAdapter` <1s, xem RSK-21 ở doc 14). **M1 phải thay Runner M0 (đồng bộ) bằng hàng đợi thật như một điều kiện tiên quyết của DAG Scheduler**, không phải một mục tuỳ chọn nằm đâu đó trong scope — ghi rõ ở §9 để không lặp lại kiểu "để sau" mà RSK-21 cảnh báo.

---

## §8 · Nguyên tắc cắt phạm vi — chi tiết theo lát cắt

Doc 13 §"Nguyên tắc ưu tiên" cho thứ tự cắt ở mức milestone (1. tính năng → 2. UI → 3. tự động hóa nâng cao → 4. không bao giờ cắt ranh giới). Ở mức lát cắt, đây là menu cụ thể — **cắt xong thì mất gì, trả nợ lúc nào**:

| Milestone | Cắt được (nhóm 1–3) | Điều kiện trả nợ | Không bao giờ cắt (nhóm 4) |
|---|---|---|---|
| M0 | Lát 5 (Agent/CLI) rút xuống chỉ `run` + `explain`, bỏ `ps`/`status`/`doctor` tối giản | Trước khi mở M1 | Lát 1–2 (State Store, Event Bus) — không có gì đứng được nếu thiếu |
| M1 | Event Bus retry/DLQ (P-M1-4) hoãn sang M2 nếu cần | Trước khi M2 cần replay để rebuild (M5) | Checkpoint/resume (P9), idempotency (REL-06) |
| M2 | Provider Ranking đầy đủ hoãn — M2 chỉ cần ưu tiên khai báo + fallback (đã ghi rõ trong P-M2-3) | Bắt buộc trước M6 | Conformance Suite, Decision Record cho mọi lựa chọn provider (dù rationale đơn giản) |
| M3 | Subtitle Agent hoãn, giữ Image + Voice | Trước M3 exit (cần ít nhất 1 agent chứng minh P4) | UC1 chạy end-to-end offline |
| M4 | Eval harness đầy đủ hoãn sang trước M6 | Trước khi đổi prompt/provider ở M5+ | 5 quy tắc chống lặp vô ích (doc 08 §4) |
| M5 | Consolidation job hoãn chạy thủ công thay vì lịch tự động | Trước M7 (cần chạy đêm ổn định) | Privacy Filter, test đối kháng L3 không rời máy |
| M6–M8 | Xem doc 13 — không có bổ sung đặc thù lát cắt ở các milestone này, nguyên tắc mức milestone là đủ |

**Quy tắc chung khi cắt một lát cắt bất kỳ:** nếu việc bị cắt đụng một trong 4 hợp đồng dài hạn (doc 04) hoặc P1/P3/P4/P10, đó không phải "cắt phạm vi" — đó là vi phạm ranh giới trá hình. Dừng và hỏi lại trước khi cắt.

---

## §9 · Bản đồ milestone → lát cắt → prompt

Doc 13 cho thời lượng và exit criteria mức milestone. Bảng này nối milestone với lát cắt thực thi và prompt tương ứng trong doc 19 — dùng khi bạn quên "M3 đang ở lát nào" sau một kỳ nghỉ dài.

| Milestone | Lát cắt chính | Prompt (doc 19) |
|---|---|---|
| Ngày 0 | Kiểm môi trường · khung repo · chốt ADR 0021–0024 | P-D0-1, P-D0-2, P-D0-3 |
| M0 | State Store · Event Bus · Process SM · Capability+Provider · Agent/CLI/Trace | P-M0-1 → P-M0-6 |
| M1 | **Điều kiện tiên quyết:** thay Runner M0 (`apps/paosd/runner.py`, chạy Agent đồng bộ trong `dispatch()`, RSK-21) bằng hàng đợi thật · Process SM đầy đủ · checkpoint/resume · DAG Scheduler · Event Bus retry/DLQ/replay · idempotency | P-M1-1 → P-M1-5 |
| M2 | Capability registry · Conformance Suite · Router+breaker · cache · Permission/Secret | P-M2-1 → P-M2-5 |
| M3 | Agent Contract+SDK · Workflow YAML engine · Video plugin (planning→script, media song song, render) | P-M3-1 → P-M3-5 |
| M4 | Rubric engine · self-correction loop · eval harness | P-M4-1 → P-M4-3 |
| M5 | Chốt ADR-0015/0016 · 5 tầng memory · consolidation · KG · Privacy Filter | P-M5-0 → P-M5-4 |
| M6 | Feature extraction+scoring · Provider ranking/EWMA · routing.yaml+explain | P-M6-1 → P-M6-3 |
| M7 | Cost Engine · Energy Engine · Time Engine | P-M7-1 → P-M7-3 |
| M8 | Chốt ADR-0017/0018 · plugin loader+sandbox · Plugin Document (bài kiểm tra kiến trúc) · Web UI · export/import | P-M8-0 → P-M8-5 |
| M9 | `web.search` + provider local · Research Agent+Workflow · Memory L4 dùng thật · Plugin Research (UC3) | P-M9-1 → P-M9-3 |
| Hardening | Đo NFR · chaos suite · sync docs↔code · chạy thật 30 ngày | P-HARD-1 → P-HARD-4 |

**Vì sao M9 xuất hiện ở đây mà không có trong bản gốc:** doc 13 ban đầu chỉ lên lịch 2/4 plugin đã hoạch định ở doc 12 §7 (Video, Document), bỏ sót Research dù UC3 (doc 01) yêu cầu nó cho v1. M9 được thêm khi rà lại tính đầy đủ của roadmap (2026-08) — xem lý do đầy đủ ở đầu mục M9 trong [doc 13](13-roadmap-and-milestones.md).

---

## §10 · Nguyên tắc "chừa cột, đừng chừa code"

Một dạng nợ kỹ thuật **có chủ đích và rẻ**: đưa vào schema/enum ngay từ M0 những gì milestone sau sẽ cần, nhưng **không** viết logic dùng chúng trước khi cần thật (vi phạm "không trừu tượng sớm" nếu làm ngược). Lý do: sửa schema sau (migration + backfill) đắt hơn nhiều so với thêm một cột rỗng lúc đầu.

Áp dụng ở M0:

| Thành phần | Có mặt từ M0 | Logic dùng nó đến |
|---|---|---|
| Bảng `checkpoints` | Schema đầy đủ (`process_id`, `seq`, `state_json`, `at`) | M1 (P-M1-2) |
| Cột `tasks.attempts`, `tasks.idempotency_key` | Có mặt, `attempts` mặc định 0 | M1 (retry thật) |
| Envelope event: `correlation_id`, `causation_id` | Đủ 10 trường (doc 05 §1) | M1+ (khi cần dựng cây nhân quả/replay) |
| Trạng thái Process `PLANNING`/`WAITING`/`PAUSED`/`COMPENSATING`/`FAILED_FINAL` | Có trong enum + bảng chuyển trạng thái | M1 (chưa có đường vào ở M0) |
| `tasks.quality_score` | Cột tồn tại, luôn `NULL` ở M0 | M4 (Review Agent) |
| `providers.breaker_state`, `providers.health` | Cột tồn tại, giá trị mặc định tĩnh | M2 (circuit breaker thật) |

**Ranh giới của nguyên tắc này:** chỉ áp dụng cho *schema* (cột, bảng, enum value) — không bao giờ áp dụng cho *code* (đừng viết class `EnergyEngine` rỗng ở M0 "để sẵn"). Một cột rỗng không tạo ra khớp nối sai; một class rỗng thì có, vì nó mời gọi người khác (hoặc chính bạn 3 tháng sau) implement sai chỗ hoặc sai hình dạng trước khi thật sự cần.
