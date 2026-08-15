#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Cổng CI 2 — Kernel độc lập (MNT-06 / RSK-03)
#
# doc 17 §2 mô tả cổng này là:
#     rm -rf providers/ agents/ plugins/ && pytest tests/kernel/
#
# Chạy nguyên văn như vậy trên máy dev sẽ XOÁ THẬT mã nguồn chưa commit.
# Script này giữ nguyên ngữ nghĩa của cổng nhưng thực hiện trong một
# `git worktree` tạm, nên an toàn ở mọi nơi — dev lẫn CI.
#
# Ý nghĩa: nếu test Kernel cần providers/ hoặc agents/ để chạy, nghĩa là
# ranh giới đã bị phá. Provider/agent giả dùng cho test Kernel PHẢI sống
# trong tests/kernel/_fakes.py (doc 18 §4.1).
# ---------------------------------------------------------------------------
set -euo pipefail

echo "── Cổng 2: Kernel độc lập (MNT-06)"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "✗ Không phải git repo. Cổng này cần git worktree để chạy an toàn."
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
WORKDIR="$(mktemp -d -t paos-isolation-XXXXXX)"

cleanup() {
    cd "$REPO_ROOT"
    git worktree remove --force "$WORKDIR" >/dev/null 2>&1 || true
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

# Bản sao sạch của HEAD + các thay đổi đang có trong index/worktree.
# Dùng stash tạo commit ảo để không đụng vào worktree thật.
STASH_SHA="$(git stash create || true)"
REF="${STASH_SHA:-HEAD}"

git worktree add --detach "$WORKDIR" "$REF" >/dev/null 2>&1
cd "$WORKDIR"

# Đây là toàn bộ ý nghĩa của cổng — xoá sạch mọi tầng trên Kernel.
# workflows/ thêm ở P-M3-2 — cùng lý do capabilities/: nội dung YAML thật của
# workflow là dữ liệu cấu hình, không phải bộ khung engine (kernel/workflow/).
rm -rf providers/ agents/ plugins/ capabilities/ apps/ workflows/

# Copy lại cấu hình test (nằm ngoài git ở một số setup)
cp "$REPO_ROOT/pyproject.toml" ./pyproject.toml 2>/dev/null || true

if [ ! -d tests/kernel ]; then
    echo "✗ Không tìm thấy tests/kernel/ — cổng này vô nghĩa nếu Kernel chưa có test."
    exit 1
fi

# Kernel phải build và test pass mà KHÔNG có bất kỳ tầng nào khác.
if PYTHONPATH="$WORKDIR" python3 -m pytest tests/kernel/ -q \
        -m "not requires_ollama and not requires_gpu"; then
    echo "✓ Kernel build + test pass khi không còn providers/, agents/, plugins/"
else
    echo ""
    echo "✗ Test Kernel phụ thuộc vào tầng bên trên. Đây là RSK-03 đang hiện thực hoá."
    echo "  Cách sửa ĐÚNG: chuyển provider/agent giả vào tests/kernel/_fakes.py"
    echo "  Cách sửa SAI:  nới lỏng cổng này."
    exit 1
fi
