# Backlog

Ý tưởng chưa chín, nợ kỹ thuật có điều kiện trả — không phải TODO ngẫu nhiên. Mỗi mục ghi rõ: phát sinh khi nào, điều kiện trả nợ là gì.

## Nợ kỹ thuật

### BL-001 · EventBus không tự retry/backoff khi subscriber lỗi
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** `_deliver_one()` (`kernel/events/bus.py`) bắt lỗi subscriber, ghi `event_deliveries.state="failed"` rồi dừng — không tự thử lại, không backoff. Event chỉ được giao lại nếu daemon restart (catch-up scan `EventBus.start()`).
**Vì sao chấp nhận tạm thời:** M0 phạm vi "Process 1 task", một subscriber (`project_logger`), lỗi subscriber ở M0 gần như luôn là lỗi lập trình (đáng thấy ngay) chứ không phải lỗi thoáng qua cần retry.
**Điều kiện trả nợ:** M1 (P-M1-4, doc 19) — thêm số lần thử + backoff có giới hạn trước khi coi là failed vĩnh viễn.

### BL-002 · Không có Dead Letter Queue + lệnh replay cho event failed vĩnh viễn
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** Event ở trạng thái `event_deliveries.state="failed"` nằm im trong bảng, không ai xem lại hay chủ động phát lại được — chỉ có thể query tay qua SQL.
**Vì sao chấp nhận tạm thời:** gắn liền BL-001 — chưa có retry thật thì DLQ cũng chưa có gì để phân biệt với "failed" thường.
**Điều kiện trả nợ:** M1 (P-M1-4, doc 19) — `paosctl events replay --from --to --to-subscriber` (đã nhắc ở doc 19 dòng 617) + view/lệnh liệt kê event trong DLQ.

### BL-003 · ProjectLogger ghi log toàn cục thay vì theo từng project
**Phát sinh:** M0 lát cắt 2 (Event Bus, 2026-08-08).
**Hiện trạng:** `make_project_logger()` (`kernel/events/bus.py`) ghi mọi event vào `workspace/.paos/logs/events.ndjson` — một file chung cho toàn hệ thống, không tách theo `projects/<x>/logs/events.ndjson` như doc 03 §4 mô tả.
**Vì sao chấp nhận tạm thời:** Process/Project (lát cắt 3+) chưa tồn tại lúc viết lát cắt 2 — chưa có gì để tách theo. Trừu tượng hóa sớm ở đây sẽ đoán sai hình dạng.
**Điều kiện trả nợ:** khi `process_id` → `project` mapping thật tồn tại. **Cập nhật (nghiệm thu M0, 2026-08-08):** M0 lát cắt 3 (Process SM) đã xong nhưng khái niệm Project vẫn CHƯA tồn tại (hoãn tới M3/M6, xem `workspace/artifacts/<process_id>/` — quy ước tạm ở `sdk/agent.py`) — điều kiện trả nợ vẫn chưa đạt, nợ này còn treo tới M3/M6, không phải M1. **Cập nhật (P-M3-2, 2026-08-15):** `WorkflowRunner._freeze_workflow_json()` (`apps/paosd/workflow_runner.py`) ghi `workflow.json` (doc 03 §4) vào `workspace/projects/<process_id>/` — CÙNG quy ước tạm (process_id thay slug), KHÔNG phải Project thật (chưa có `project.json`/`job.json`/slug người đọc được). Điều kiện trả nợ vẫn chưa đạt, giờ có 2 chỗ dùng quy ước tạm này (artifacts/ và projects/) thay vì 1 — càng nên trả sớm ở M6.

### BL-005 · Workflow (P-M3-2) không resume từng-step sau crash — restart chạy lại TOÀN BỘ DAG từ đầu
**Phát sinh:** P-M3-2 (Workflow YAML engine, 2026-08-15).
**Hiện trạng:** `Runner._run_workflow()` gọi `WorkflowRunner.run()` mỗi lần `_run_one()` chạy — kể cả khi process đã ở RUNNING từ trước (resume sau crash daemon, doc 19 P-M1-4). Khác agent đơn (P-M3-1, có `agent.resume()` + kiểm `checkpoint_seq > 1`), `WorkflowRunner.run()` luôn dựng `context` rỗng và chạy lại `topological_order(spec)` từ step ĐẦU TIÊN — dù nhiều step trước đó đã có Task ở SUCCEEDED trong DB (`TaskStore.get_by_step()` chỉ được dùng để tránh vi phạm UNIQUE(idempotency_key) khi retry/loop-back, KHÔNG được dùng để bỏ qua step đã xong khi resume).
**Vì sao chấp nhận tạm thời:** P-M3-2 tập trung chứng minh cơ chế DAG (điều kiện, parallel, retry, vòng lặp, compensation) chạy đúng trong 1 lượt liên tục — đủ cho UC1 chạy 1 lần không bị crash giữa chừng (exit criteria M3 chưa yêu cầu resume-giữa-DAG). Thêm resume-per-step ngay bây giờ là trừu tượng hoá sớm khi chưa có ca dùng thật cần nó (P4).
**Điều kiện trả nợ:** khi có nhu cầu thật (workflow chạy đủ lâu — vd render video M3-5 — để crash-giữa-chừng-rồi-restart trở thành tình huống đáng lo, hoặc khi viết test đối kháng "kill -9 giữa workflow" như đã làm cho Process đơn ở M1-4). Cách trả: trước khi chạy mỗi step trong `_execute_chain()`, kiểm `TaskStore.get_by_step()` — nếu đã SUCCEEDED, nạp lại `output` đã lưu (cần thêm cột lưu output hoặc đọc lại từ artifact) vào `context` rồi bỏ qua, không gọi lại Capability/Agent.

### BL-006 · ~~`apps/paosd/runner.py::_AGENTS` vẫn là dict hardcode~~ — ĐÃ TRẢ ở P-M3-4
**Phát sinh:** M0 lát cắt 5c (1 agent) · vẫn còn ở P-M3-3 (2026-08-15) khi bảng đã có 3 agent thật (summarize, planning, script).
**Hiện trạng (trước khi trả):** Thêm agent mới vẫn phải sửa `apps/paosd/runner.py` (thêm import + 1 entry vào `_AGENTS`) — khác hẳn provider (`Registry.load_adapter()` nạp động qua `importlib`, đọc `provider.yaml::adapter`, thêm provider mới KHÔNG sửa code, đúng exit criteria M2). Agent chưa có cơ chế tương đương.
**Đã trả (P-M3-4, 2026-08-15):** `Registry.load_agent()`/`agent_extra_dir()`/`preload_agent()` (`kernel/registry/registry.py`) nạp động Agent qua `agents/<x>/manifest.yaml::entry` (dạng `module.path:ClassName`, giống hệt `load_adapter()`). `apps/paosd/runner.py` không còn `_AGENTS`, không còn `from agents.xxx.agent import ...` tĩnh nào — `_resolve_agent()` tra Registry trực tiếp. `WorkflowRunner` bỏ hẳn tham số `agents:` truyền tay, dùng `self._registry` sẵn có. Lưu ý cổng 1 (Kernel sạch AI): `agent_extra_dir()` trả về thư mục GỐC của agent, không tự nối `"prompts"` (chữ đó vi phạm `PKG_AI` regex nếu xuất hiện trong `kernel/`) — `apps/` tự nối đường dẫn con cần dùng.
**Bằng chứng P4:** thêm Image/Voice/Subtitle Agent (cùng lát cắt) không sửa `apps/paosd/runner.py` lẫn `apps/paosd/workflow_runner.py` — chỉ thêm file mới dưới `agents/`.

### BL-004 · Ollama chưa an toàn để `Registry.load_adapter()` nạp — 2 nợ liên quan
**Phát sinh:** M0 lát cắt 4c (Ollama adapter) + phát hiện lại khi dựng Conformance Suite (P-M2-2, 2026-08-10) + THỬ ĐÓNG rồi revert lại khi dựng Router (P-M2-3, 2026-08-14).
**Hiện trạng:**
1. `providers/ollama/provider.yaml` CỐ Ý chưa khai `adapter:`. `apps/paosd/router.py::Router` (P-M2-3) giờ ĐÃ có fallback thật theo kết quả `invoke()` (không chỉ theo `load()` như P-M2-1) — về lý thuyết đủ để bật `adapter:` an toàn. Đã THỬ THẬT: bật lên làm mọi test golden-path (Registry trỏ tới `providers/` thật, nên `providers_for("text.generate", 1)` trả về CẢ ollama lẫn stub) thử gọi Ollama thật (`localhost:11434`) trước khi rơi về stub — chậm, không tất định, và chèn thêm event `capability.fallback.triggered` làm vỡ assertion thứ tự event ở nhiều test (`test_runner`, `test_determinism`, `test_scheduler`...). Router đúng, nhưng thiếu hạ tầng test để override/loại trừ CẢ Ollama (không chỉ stub) khi test không cần nó.
2. `OllamaAdapter.cancel()` (`providers/ollama/adapter.py`) là no-op tuyệt đối — không dừng request `httpx` đang chạy, khác `StubAdapter` (đã có cancel thật từ P-M2-2).
**Vì sao chấp nhận tạm thời:** mục 1 cần thêm cách nào đó để test golden-path không phụ thuộc network timing thật của MỌI provider đăng ký cho 1 capability (vd `adapter_overrides` áp cho tất cả provider trùng capability, hoặc registry test-mode loại provider cần dịch vụ ngoài) — lớn hơn phạm vi "1 dòng YAML", chưa làm ở P-M2-3. Mục 2 cần seam abort request httpx đang chạy.
**Điều kiện trả nợ:** mục 1 — khi có hạ tầng test cách ly khỏi network thật cho MỌI provider trùng capability (không riêng gì test đang override). Mục 2 — khi có nhu cầu cancel thật với Ollama chạy dài.

### BL-007 · Instance Agent bị CACHE và DÙNG CHUNG giữa các Process chạy song song — race condition trên state instance (vd `self._ctx`)
**Phát sinh:** phát hiện khi thiết kế Render Agent (P-M3-5, 2026-08-15), nhưng lỗi tồn tại từ M0 (SummarizeAgent) — chỉ lộ rõ khi rà lại đường dữ liệu lúc thêm agent thứ 6.
**Hiện trạng:** `Registry.load_agent()` (P-M3-4) cache 1 INSTANCE agent theo `agent_id@version`, dùng lại cho MỌI lần gọi (đúng ý đồ — giống `load_adapter()` cho provider, tránh khởi tạo lại tốn kém). Nhưng Agent — khác Provider Adapter — có STATE riêng theo từng lượt chạy: mọi agent (`SummarizeAgent`, `PlanningAgent`... `RenderAgent`) đều gán `self._ctx = ctx` trong `initialize()` rồi đọc lại `self._ctx` ở `execute()`/`publish()`; `RenderAgent` còn gán thêm `self._images`/`self._voice`/`self._subtitle` trong `think()`. `Runner.worker_loop()` (M1-3a) chạy NHIỀU Process thật sự đồng thời (`asyncio.Semaphore(max_parallel)`, mặc định 3) — nếu 2 Process cùng dùng 1 `agent_id@version` (vd 2 job "tóm tắt" chạy cùng lúc), cả hai chia sẻ CÙNG MỘT instance `SummarizeAgent`, và các `await` xen kẽ giữa `initialize()`/`execute()`/`publish()` của 2 lượt gọi có thể làm `self._ctx` (hoặc `self._images`...) của lượt A bị lượt B ghi đè giữa chừng — Process A có thể publish nhầm dữ liệu/context của Process B.
**Vì sao chấp nhận tạm thời:** đây là lỗi kiến trúc rộng (ảnh hưởng MỌI agent, không riêng Render), sửa đúng cần đổi cách Registry cấp phát Agent (nạp CLASS + tự khởi tạo INSTANCE MỚI mỗi lần gọi, thay vì cache instance dùng chung — khác hẳn Provider Adapter vốn không có state riêng theo lượt gọi, `sdk/provider.py::ProviderAdapter.invoke()` nhận đủ dữ liệu qua tham số, không qua `self`). Sửa lan ra `Registry.load_agent()`, `apps/paosd/runner.py::_resolve_agent()`, `apps/paosd/workflow_runner.py::_run_agent_step()` — lớn hơn phạm vi 1 agent, và CHƯA CÓ bằng chứng bug đã hiện thực hoá thật (chưa có test 2 Process cùng agent_id chạy song song thật để xác nhận) — cần xác nhận trước khi sửa, tránh sửa nhầm hướng.
**Điều kiện trả nợ:** viết test đối kháng "2 Process cùng dùng 1 agent_id@version, chạy đồng thời thật (`asyncio.gather`), assert artifact của Process A không lẫn dữ liệu Process B" — nếu đỏ (xác nhận bug thật), sửa `Registry.load_agent()` không cache instance (hoặc cache CLASS, khởi tạo instance mới mỗi lần `load_agent()` được gọi — instance nhẹ, không có state khởi tạo tốn kém giống ProviderAdapter nên tạo mới mỗi lần chấp nhận được).

---

## Mục chưa phân loại

*(trống)*
