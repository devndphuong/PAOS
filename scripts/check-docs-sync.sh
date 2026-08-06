#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Cổng CI 6 — Tài liệu đồng bộ với code (MNT-09)
#
# doc 17 §8: "docs/ là nguồn sự thật, không phải ghi chú phụ. Code lệch tài
# liệu = bug." Cổng này CẢNH BÁO, chưa chặn — theo doc 18 §4.4.
#
# Nâng lên mức chặn (exit 1) khi nào: sau khi đóng M2, tức khi cả 4 hợp đồng
# dài hạn đã ổn định và việc sửa chúng phải là hành động có cân nhắc.
# ---------------------------------------------------------------------------
set -uo pipefail

BASE="${1:-origin/main}"

# Các đường dẫn được coi là "chạm vào hợp đồng dài hạn" (P10 / doc 04)
CONTRACT_PATHS=(
    "kernel/state/migrations/"
    "capabilities/"
    "schemas/events/"
    "sdk/"
    "apps/paosd/routes"
)

echo "── Cổng 6: docs đồng bộ (MNT-09, mức cảnh báo)"

if ! git rev-parse --verify "$BASE" >/dev/null 2>&1; then
    echo "ℹ  Không có ref '$BASE' để so sánh — bỏ qua."
    exit 0
fi

CHANGED="$(git diff --name-only "$BASE"...HEAD || true)"
[ -z "$CHANGED" ] && { echo "✓ không có thay đổi"; exit 0; }

TOUCHED_CONTRACT=""
for p in "${CONTRACT_PATHS[@]}"; do
    hits="$(echo "$CHANGED" | grep -E "^${p}" || true)"
    [ -n "$hits" ] && TOUCHED_CONTRACT+="$hits"$'\n'
done

TOUCHED_DOCS="$(echo "$CHANGED" | grep -E '^docs/' || true)"

if [ -n "$TOUCHED_CONTRACT" ] && [ -z "$TOUCHED_DOCS" ]; then
    echo ""
    echo "⚠  PR này đụng vào hợp đồng dài hạn nhưng không sửa docs/:"
    echo "$TOUCHED_CONTRACT" | sed 's/^/     /'
    echo ""
    echo "   Kiểm tra lại:"
    echo "     • Có cần cập nhật doc 03 (schema) / 04 (contract) / 05 (event) không?"
    echo "     • Đây có phải quyết định cần ADR không? (doc 17 §7)"
    echo "     • Có cần migration + deprecation 2 phiên bản không? (doc 02 §9)"
    echo ""
    echo "   Nếu thật sự không cần sửa docs — bỏ qua cảnh báo này."
    exit 0
fi

echo "✓ đồng bộ"
exit 0
