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

### BL-007 · ~~Instance Agent bị CACHE và DÙNG CHUNG giữa các Process chạy song song~~ — ĐÃ TRẢ (2026-08-15)
**Phát sinh:** phát hiện khi thiết kế Render Agent (P-M3-5, 2026-08-15), nhưng lỗi tồn tại từ M0 (SummarizeAgent) — chỉ lộ rõ khi rà lại đường dữ liệu lúc thêm agent thứ 6.
**Hiện trạng (trước khi trả):** `Registry.load_agent()` (P-M3-4) cache 1 INSTANCE agent theo `agent_id@version`, dùng lại cho MỌI lần gọi (đúng ý đồ — giống `load_adapter()` cho provider, tránh khởi tạo lại tốn kém). Nhưng Agent — khác Provider Adapter — có STATE riêng theo từng lượt chạy: mọi agent (`SummarizeAgent`, `PlanningAgent`... `RenderAgent`) đều gán `self._ctx = ctx` trong `initialize()` rồi đọc lại `self._ctx` ở `execute()`/`publish()`; `RenderAgent` còn gán thêm `self._images`/`self._voice`/`self._subtitle` trong `think()`. `Runner.worker_loop()` (M1-3a) chạy NHIỀU Process thật sự đồng thời (`asyncio.Semaphore(max_parallel)`, mặc định 3) — nếu 2 Process cùng dùng 1 `agent_id@version`, cả hai chia sẻ CÙNG MỘT instance.

**Xác nhận bug THẬT (không phải lo ngại lý thuyết):** test đối kháng `tests/apps/paosd/test_runner.py::test_concurrent_processes_same_agent_do_not_corrupt_each_other` — 3 Process cùng `agent:summarize.agent@1`, văn bản đầu vào KHÁC nhau, chạy đồng thời thật qua `worker_loop()` (`PAOS_STUB_DELAY_MS` ép `StubAdapter.invoke()` await thật để mở khe hở race, không mock gì khác). Trên code cũ, test ĐỎ NGAY LẦN CHẠY ĐẦU: process đầu tiên hoàn toàn KHÔNG có event `summary.created` của chính nó (0, không phải 1) — dữ liệu bị publish nhầm sang process khác. Đúng cơ chế đã dự đoán: `self._ctx` bị `initialize()` của process chạy sau ghi đè trước khi `publish()` của process chạy trước kịp đọc lại.

**Đã trả (2026-08-15):** `Registry.load_agent()` (`kernel/registry/registry.py`) không còn cache instance dùng chung — tách 2 cache riêng: `_agent_preload` (giữ hành vi `preload_agent()` cũ, instance CỐ ĐỊNH chỉ dùng cho test tự giữ tham chiếu điều khiển được) và `_agent_class_cache` (cache CLASS đã `importlib.import_module()`, KHÔNG cache instance) — `load_agent()` tự `agent_cls()` khởi tạo instance MỚI mỗi lần được gọi. `apps/paosd/runner.py::_resolve_agent()` và `apps/paosd/workflow_runner.py::_run_agent_step()` đã gọi `registry.load_agent()` MỚI mỗi lần cần agent (1 lần/Process, 1 lần/step) từ trước — không cần sửa gì ở 2 nơi này. `load_adapter()`/`_adapter_cache` giữ NGUYÊN không đụng tới (ProviderAdapter không có state riêng theo lượt gọi, không cùng vấn đề). Thêm test đơn vị `tests/kernel/test_registry.py::test_load_agent_dynamically_imports_and_returns_new_instance_each_call` (+ 3 test phụ not-found/missing-entry/preload) khẳng định rõ hợp đồng hành vi mới. Test đối kháng ở trên GIỜ XANH ổn định (chạy lặp lại nhiều lần không flaky). `make ci` xanh đủ 6 cổng, coverage 95.17%.

### BL-008 · Self-correction loop (P-M4-2) chưa cưỡng chế được quy tắc 3 (ngân sách retry ≤ 30% ngân sách Job)
**Phát sinh:** P-M4-2 (2026-08-16, doc 19) — dựng `apps/paosd/workflow_runner.py::WorkflowRunner._run_self_correction` (step kind `self_correction`, `kernel/workflow/spec.py`) theo 5 quy tắc chống lặp vô ích của doc 08 §4.
**Hiện trạng:** 4/5 quy tắc đã cưỡng chế thật (giới hạn số lần thử, dừng sớm khi không cải thiện ≥ `min_improvement`, bắt buộc đổi chiến lược ở lượt cuối, escalate luôn kèm bản tốt nhất). Quy tắc 3 ("ngân sách retry riêng ≤ 30% ngân sách Job") **CHƯA làm được** — Job hiện chưa có khái niệm "ngân sách" nào để so sánh (Cost Engine estimate/record/budget 3 tầng là phạm vi M7, doc 13), nên vòng lặp có thể chạy tối đa `max_loops + 1` lần thử mà không hề biết đã tiêu bao nhiêu so với ngân sách tổng.
**Vì sao chấp nhận tạm thời:** không có gì để so sánh — thêm 1 hằng số ngân sách giả (hardcode) sẽ là "giả vờ làm" thay vì làm thật (P8, THẬT THÀ), và Cost Engine (M7) chưa tồn tại để cung cấp con số ngân sách Job thật. Rủi ro thực tế thấp ở M4: quy tắc 1 (giới hạn số lần thử tuyệt đối, mặc định tối đa 3) đã tự nó chặn được lặp vô hạn/cháy chi phí không kiểm soát (RSK-09) — quy tắc 3 chỉ siết chặt thêm khi có ngân sách THẬT để so sánh.
**Điều kiện trả nợ:** khi Cost Engine (M7) có `budget_left` thật truyền được qua `AgentContext`/`CallContext` — thêm kiểm tra "chi phí self_correction tới nay > 30% ngân sách Job" vào vòng lặp `_run_self_correction`, dừng + escalate sớm nếu vượt, dù chưa hết `max_loops`.

### BL-009 · self_correction (P-M4-2) chưa nối vào workflow UC1 thật (`video.plan_and_script`)
**Phát sinh:** P-M4-2 (2026-08-16, doc 19).
**Hiện trạng:** `kind: self_correction` (rubric engine + Review Agent + vòng lặp 5 quy tắc) mới chỉ chạy trong `workflows/script_with_review/1/workflow.yaml` — một workflow ĐỘC LẬP dựng riêng để chứng minh cơ chế (test `tests/apps/paosd/test_self_correction.py`), KHÔNG phải nhánh Script trong `workflows/video.plan_and_script/` (UC1 thật, M3). Video pipeline vẫn dùng `kind: agent, ref: script.agent@1` trực tiếp — Script Agent output không được tự sửa/chấm điểm trong luồng UC1 thật.
**Vì sao chấp nhận tạm thời:** doc 13 M4 exit criteria ("Script kém bị reject, vòng 2 cải thiện, vòng 3 đổi chiến lược") không bắt buộc phải chứng minh NGAY TRONG UC1 — một workflow độc lập chứng minh cơ chế THẬT (không mock rubric engine, không mock agent) là đủ bằng chứng cho M4, và tách rời giảm rủi ro (sửa `video.plan_and_script` đang chạy tốt ở M3 để nhúng thêm 1 cơ chế mới lớn, ngay trong cùng lát cắt dựng cơ chế đó, là rủi ro không cần thiết).
**Điều kiện trả nợ:** khi có nhu cầu thật (UC1 sản xuất video cần chất lượng script cao hơn) — tạo `workflows/video.plan_and_script/4/workflow.yaml` (đóng băng bản 3, không sửa — doc 04 §6) thay step `script` (`kind: agent`) bằng `kind: self_correction`.

### BL-010 · `scripts/run_eval.py` không cưỡng chế "judge khác provider generator"
**Phát sinh:** P-M4-3 (2026-08-16, doc 19).
**Hiện trạng:** Eval harness standalone (`scripts/run_eval.py`) gọi thẳng 1 `ProviderAdapter` instance cho cả sinh script lẫn chấm judge — không qua `apps/paosd/router.py::Router`, nơi DUY NHẤT cưỡng chế `exclude_provider` thật (ADR-0008/RSK-10, đã kiểm ở `tests/apps/paosd/test_self_correction.py::test_judge_excludes_generator_provider`). Script chỉ dùng cho SO SÁNH offline nhanh (không publish artifact thật), không phải luồng self-correction production (đó vẫn đi qua Router, không bị ảnh hưởng bởi nợ này).
**Vì sao chấp nhận tạm thời:** dựng lại cả Router/Registry cho 1 script so sánh offline là quá nặng so với lợi ích — RSK-10 (LLM tự chấm điểm mình) rủi ro thấp hơn nhiều ở NGỮ CẢNH so sánh tương đối 2 config (không phải quyết định publish/reject 1 artifact thật).
**Điều kiện trả nợ:** nếu phát hiện eval harness cho điểm thiên vị rõ rệt (vd luôn ưu ái config vừa sinh) — khi đó cho `scripts/run_eval.py` dựng `Registry`/`Router` thật thay vì gọi thẳng adapter.

### BL-011 · Dataset eval (`tests/eval/datasets/script_writing_vi.jsonl`) chỉ có 6 mẫu, chưa phải 30-50 mẫu quy mô đầy đủ
**Phát sinh:** P-M4-3 (2026-08-16, doc 19), doc 18 §8 đã ghi trước: "Eval harness đầy đủ hoãn sang trước M6".
**Hiện trạng:** Dataset seed 6 mẫu đủ để chứng minh cơ chế harness chạy đúng (`tests/eval/test_eval_suite.py`) và dùng thử `scripts/run_eval.py`, nhưng chưa đủ quy mô doc 08 §7.5 mô tả ("30-50 mẫu có nguồn + kỳ vọng") để tin cậy số liệu so sánh prompt/provider trong quyết định thật. `tests/eval/rubrics/script_eval.rubric.v1.yaml` cũng dùng cửa sổ `length` lỏng hơn bản production (40-120 từ thay vì 150-200) để khớp dataset ngắn hiện tại.
**Vì sao chấp nhận tạm thời:** đúng phạm vi đã cắt sẵn ở doc 18 §8 — hạ tầng (sdk/eval.py, harness, edit_rate recording) là phần BẮT BUỘC ở M4; mở rộng dataset lên quy mô đầy đủ là công việc lặp lại đơn giản (thêm dòng JSONL), không phải rủi ro kiến trúc, hợp lý để làm dần khi có nhu cầu thật trước M6.
**Điều kiện trả nợ:** trước M6 (Provider Ranking, doc 13) — khi đó số liệu eval bắt đầu ảnh hưởng quyết định routing thật, cần dataset đủ lớn để đáng tin. Khi mở rộng, đổi `tests/eval/rubrics/script_eval.rubric.v1.yaml` lại dùng đúng cửa sổ 150-200 từ của bản production, hoặc xoá hẳn bản riêng nếu dataset mới đã khớp.

### BL-012 · Self-correction escalation (`quality.escalated.to_human`) chưa ghi Decision Record
**Phát sinh:** phát hiện khi rà lại `apps/paosd/workflow_runner.py::_run_self_correction` lúc thiết kế P-M4-3 (2026-08-16) — lỗi/thiếu sót tồn tại từ P-M4-2, không phải do lát cắt này gây ra.
**Hiện trạng:** ADR-0014 (doc 15) liệt "quyết định retry/escalate/đổi chiến lược" là 1 trong 4 nơi BẮT BUỘC có Decision Record (doc 10 §"Decision Record"). `_run_self_correction` chỉ phát event `quality.escalated.to_human`, không ghi hàng nào vào bảng `decisions` (migration 003) khi quyết định dừng vòng lặp/escalate — khác `apps/paosd/router.py::_write_decision` (chọn provider) đã tuân thủ đúng ADR-0014.
**Vì sao chấp nhận tạm thời:** event `quality.escalated.to_human` đã mang đủ thông tin audit tối thiểu (best_score, attempts, reason) cho M4 exit criteria; đây là nợ tuân thủ ADR-0014 chưa gây hư hại chức năng nào, không thuộc phạm vi P-M4-3 (eval harness/edit_rate).
**Điều kiện trả nợ:** lát cắt tiếp theo chạm `_run_self_correction` — thêm 1 hàng `decisions` (`scope='self_correction_escalation'`, `candidates_json` = điểm từng vòng, `chosen`='escalate', `rationale`=lý do) ngay trước khi phát event, cùng khuôn mẫu `router.py::_write_decision`.

### BL-013 · `MemoryRetriever` chưa triển khai bước 2 (Knowledge Graph walk) của truy hồi lai
**Phát sinh:** P-M5-1 (2026-08-16, doc 19).
**Hiện trạng:** `apps/paosd/memory_retriever.py::MemoryRetriever.search()` chỉ triển khai 4/5 bước doc 07 §3: (1) exact key lookup, (3) vector search, (4) recency boost, (5) rerank + ngân sách token. Bước (2) — "Knowledge Graph walk (2 hop từ entity trong yêu cầu)" — hoàn toàn chưa có, vì Knowledge Graph (`kg_nodes`/`kg_edges`, doc 03 §3) chưa tồn tại.
**Vì sao chấp nhận tạm thời:** đúng thứ tự milestone đã lên kế hoạch — Knowledge Graph là P-M5-3, sau P-M5-1 (memory + retrieval) này. Xây bước 2 trước khi có KG là trừu tượng hoá cho dữ liệu chưa tồn tại (P4).
**Điều kiện trả nợ:** P-M5-3 — sau khi `kg_nodes`/`kg_edges` có dữ liệu thật, thêm bước walk 2-hop vào `search()` giữa bước 1 và bước 3, merge kết quả cùng cách bước 3 đang làm (seen_ids tránh trùng lặp).

### BL-014 · `memory_items` có cột `expires_at` (TTL cho L4) nhưng chưa có job dọn hết hạn
**Phát sinh:** P-M5-1 (2026-08-16, doc 19).
**Hiện trạng:** doc 07 §1 định nghĩa L4 (World Cache) "Xóa khi: TTL hết hạn". Migration 007 đã có cột `memory_items.expires_at` (đúng schema doc 03 §3) và `MemoryStore.write()` nhận `expires_at` khi ghi, nhưng KHÔNG có job/cơ chế nào đọc cột đó rồi dọn item đã hết hạn — dữ liệu L4 cứ tích tụ vô hạn.
**Vì sao chấp nhận tạm thời:** chưa có caller thật nào ghi L4 với `expires_at` (P-M5-1 chỉ dựng hạ tầng lưu trữ/truy hồi, chưa có KnowledgeExtractor/ingestion pipeline thật ghi L4 — đó là P-M5-3). Xây job dọn rác cho dữ liệu chưa từng được ghi là trừu tượng hoá sớm.
**Điều kiện trả nợ:** khi có caller thật ghi L4 (P-M5-3 KnowledgeExtractor, hoặc M9 Research Plugin) — thêm vào Consolidation Job hàng đêm (P-M5-2, doc 07 §5.2) một bước "xoá `memory_items` WHERE `tier='L4' AND expires_at < now`", cùng chỗ đã có `consolidation job chạy mỗi đêm`.

---

## Mục chưa phân loại

*(trống)*
