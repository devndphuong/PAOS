.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PY      := python3
PKG_AI  := openai|anthropic|gpt|claude|ollama|comfyui|llm|prompt
PKG_PRV := provider_id|model\s*=|api_key

# ===========================================================================
# Vòng lặp phát triển hằng ngày
# ===========================================================================

.PHONY: install
install: ## Cài phụ thuộc dev vào venv hiện tại
	$(PY) -m pip install -e ".[dev]"

.PHONY: format
format: ## Tự sửa định dạng
	ruff format .
	ruff check --fix .

.PHONY: lint
lint: ## Kiểm định dạng + lint (doc 17 §3)
	ruff format --check .
	ruff check .

.PHONY: type
type: ## mypy --strict cho kernel/ và sdk/
	mypy

.PHONY: arch
arch: ## Cưỡng chế quy tắc phụ thuộc (doc 17 §1)
	lint-imports --config .importlinter

.PHONY: test
test: ## Test nhanh — loại những thứ cần môi trường ngoài
	pytest -m "not requires_ollama and not requires_gpu and not slow and not chaos and not eval"

.PHONY: test-all
test-all: ## Toàn bộ test, kể cả cần Ollama và chaos
	pytest

.PHONY: cov
cov: ## Test kèm coverage Kernel (MNT-07 >= 85%)
	pytest --cov --cov-report=term-missing \
		-m "not requires_ollama and not requires_gpu and not eval"

.PHONY: eval
eval: ## Eval suite (doc 08 §7.5) — chạy trước khi đổi prompt/provider, hoặc hàng đêm
	pytest -m eval -v

# ===========================================================================
# Bốn cổng CI không thể bỏ qua — doc 17 §2
#
# Nếu một ngày bạn muốn tắt một trong bốn cổng này để merge nhanh:
# đó chính là khoảnh khắc dự án bắt đầu chết. Hãy sửa code, đừng sửa Makefile.
# ===========================================================================

.PHONY: gate1
gate1: ## Cổng 1 — Kernel sạch AI (P1 / ADR-0009 / MNT-01)
	@echo "── Cổng 1: Kernel sạch AI"
	@if grep -rniE "$(PKG_AI)" kernel/ --include="*.py" ; then \
		echo ""; \
		echo "✗ Kernel chứa từ khoá thuộc tầng AI."; \
		echo "  Sửa CODE, đừng sửa cổng. Gợi ý thay thế: 'capability', 'payload',"; \
		echo "  'nội dung sinh ra', 'yêu cầu'. Xem doc 18 §4.4 và §6 R29."; \
		exit 1; \
	else \
		echo "✓ sạch"; \
	fi

.PHONY: gate2
gate2: ## Cổng 2 — Kernel độc lập (MNT-06). An toàn: chạy trong git worktree tạm.
	@bash scripts/ci-kernel-isolation.sh

.PHONY: gate3
gate3: ## Cổng 3 — Agent mù provider (P3)
	@echo "── Cổng 3: Agent mù provider"
	@if [ ! -d agents ]; then echo "✓ chưa có agents/"; exit 0; fi; \
	if grep -rniE "$(PKG_PRV)" agents/ --include="*.py" ; then \
		echo ""; \
		echo "✗ Agent biết đến provider/model. Vi phạm P3 — PR bị từ chối."; \
		echo "  Lưu ý: 'model_config' của pydantic cũng khớp. Đổi tên biến."; \
		exit 1; \
	else \
		echo "✓ sạch"; \
	fi

.PHONY: gate4
gate4: ## Cổng 4 — Không secret trong log (SEC-01)
	@echo "── Cổng 4: Redaction"
	pytest tests/security/test_redaction.py -q

.PHONY: gate5
gate5: ## Cổng 5 — Mọi event phát ra đều có schema đã đăng ký (doc 08 §7.2)
	@$(PY) scripts/check-event-schemas.py

.PHONY: gate6
gate6: ## Cổng 6 — Sửa contract phải sửa docs (MNT-09, cảnh báo)
	@bash scripts/check-docs-sync.sh

.PHONY: gates
gates: gate1 gate2 gate3 gate4 gate5 gate6 ## Chạy cả 6 cổng

.PHONY: ci
ci: lint type arch cov gates ## Toàn bộ kiểm tra như CI chạy
	@echo ""
	@echo "════════════════════════════════════════"
	@echo " CI xanh. main giữ được trạng thái chạy."
	@echo "════════════════════════════════════════"

# ===========================================================================
# Nghi thức kết thúc phiên & milestone
# ===========================================================================

.PHONY: session-end
session-end: ci ## Trước khi đóng máy: CI xanh + nhắc ghi worklog
	@echo ""
	@echo "Còn 2 việc trước khi đóng máy:"
	@echo "  1. Ghi 3 dòng vào docs/worklog.md (đã làm / đang vướng / tiếp theo)"
	@echo "  2. Commit theo Conventional Commits — main phải chạy được"

.PHONY: ten-year-test
ten-year-test: ## Bài kiểm tra 10 năm (mỗi quý) — doc 02 §9
	@echo "── Thay toàn bộ provider bằng stub, chạy contract test"
	PAOS_MODE=deterministic PAOS_FORCE_PROVIDER_CLASS=stub pytest tests/contract/ -q
	@$(MAKE) gate2

.PHONY: clean
clean: ## Dọn rác build/test
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: help
help: ## Danh sách target
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'
