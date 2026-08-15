#!/usr/bin/env python3
"""Cổng CI 5 — mọi event phát ra đều phải có schema đã đăng ký.

doc 08 §7.2 coi đây là test bắt buộc; doc 05 gọi Event Schema là hợp đồng dài
hạn (P10). Cổng này rẻ khi repo còn 10 event, và gần như không cài lại được
khi đã có 200.

Nguyên tắc thi hành
-------------------
1. Mọi tên event phải được khai báo tập trung trong ``kernel/events/types.py``
   dưới dạng ``class EventType(StrEnum)``. Không nơi nào được viết chuỗi tên
   event trần.
2. Mỗi tên event phải có đúng một file ``schemas/events/<type>.v<n>.schema.json``.
3. Ngược lại, mỗi file schema phải ứng với một thành viên của enum — schema
   mồ côi nghĩa là event đã bị xoá mà hợp đồng còn sót lại.
4. Manifest của Agent/Provider (``emits:``) cũng chỉ được nêu tên đã đăng ký.
5. Tên event phải theo quy ước doc 05 §2: ``<domain>.<entity>.<action-quá-khứ>``.

Chạy: ``python3 scripts/check-event-schemas.py``  ·  exit 0 = xanh
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES_FILE = ROOT / "kernel" / "events" / "types.py"
SCHEMA_DIR = ROOT / "schemas" / "events"

# doc 05 §2 — thì quá khứ bắt buộc, không đặt tên kiểu mệnh lệnh.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_*]+){1,3}$")
PAST_TENSE_OK = {
    "created",
    "started",
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "queued",
    "paused",
    "resumed",
    "checkpointed",
    "received",
    "scheduled",
    "retried",
    "selected",
    "skipped",
    "entered",
    "triggered",
    "made",
    "passed",
    "crashed",
    "installed",
    "startup",
    "shutdown",
    "learned",
    "extracted",
    "recorded",
    "exceeded",
    "deferred",
    "approved",
    "denied",
    # M1-1 (doc 19 P-M1-1): event đánh dấu VỪA CHUYỂN vào một trạng thái có tên
    # dạng -ing (PLANNING/WAITING/COMPENSATING) — tiếp tục tiền lệ đã có với
    # "queued"/"paused"/"resumed": dùng lại tên trạng thái làm tên event, thay
    # vì bịa một động từ quá khứ gượng ép không ai dùng trong hội thoại thật.
    "planning",
    "waiting",
    "compensating",
    "failed_final",
    # P-M2-5 (doc 09 dòng 49, 56) — tên event lấy NGUYÊN VĂN từ doc 09, cả 2 đều
    # là thì quá khứ hợp lệ ("bị chặn", "đã được yêu cầu").
    "blocked",
    "requested",
    # P-M3-1 — "progress" lấy NGUYÊN VĂN từ doc 05 §3.1 (tên event đã định sẵn
    # từ đầu dự án), cùng tiền lệ với "planning"/"waiting": báo cáo TRẠNG THÁI
    # tại một thời điểm, không phải hành động đã hoàn tất, nên không có dạng
    # quá khứ tự nhiên — bịa một động từ quá khứ gượng ép sẽ tệ hơn giữ nguyên.
    "progress",
    # P-M3-5 — "rendered" (video.rendered), thì quá khứ hợp lệ, lấy nguyên văn
    # từ doc 05 §3.4 (đã định sẵn từ đầu dự án).
    "rendered",
}

errors: list[str] = []
warnings: list[str] = []


def declared_event_types() -> set[str]:
    """Đọc giá trị các thành viên EventType từ AST — không import module."""
    if not TYPES_FILE.exists():
        errors.append(
            f"Không tìm thấy {TYPES_FILE.relative_to(ROOT)}. "
            "Mọi tên event phải khai báo tập trung ở đây (doc 05)."
        )
        return set()

    tree = ast.parse(TYPES_FILE.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "EventType":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                if isinstance(stmt.value.value, str):
                    found.add(stmt.value.value)
    if not found:
        errors.append("Không tìm thấy thành viên nào trong class EventType.")
    return found


def schema_files() -> dict[str, list[Path]]:
    """Ánh xạ tên event -> danh sách file schema theo version."""
    if not SCHEMA_DIR.exists():
        errors.append(
            f"Không tìm thấy {SCHEMA_DIR.relative_to(ROOT)}/. "
            "Event Schema là hợp đồng (P10), phải nằm dưới dạng file JSON Schema."
        )
        return {}

    mapping: dict[str, list[Path]] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        stem = path.name.removesuffix(".schema.json")
        m = re.match(r"^(?P<name>.+)\.v(?P<ver>\d+)$", stem)
        if not m:
            errors.append(f"{path.name}: tên file phải dạng <event.type>.v<n>.schema.json")
            continue
        mapping.setdefault(m["name"], []).append(path)
    return mapping


def check_naming(names: set[str]) -> None:
    for name in sorted(names):
        if not NAME_RE.match(name):
            errors.append(
                f"'{name}': sai quy ước doc 05 §2 "
                "(<domain>.<entity>.<action-quá-khứ>, chữ thường, phân tách bằng dấu chấm)"
            )
            continue
        action = name.rsplit(".", 1)[-1]
        if action not in PAST_TENSE_OK:
            warnings.append(
                f"'{name}': hành động '{action}' không nằm trong danh sách thì quá khứ đã biết. "
                "Nếu đây là mệnh lệnh — bạn đang muốn RPC, hãy dùng Workflow (doc 05 §2). "
                "Nếu là thì quá khứ hợp lệ, bổ sung vào PAST_TENSE_OK trong script này."
            )


def check_manifests(declared: set[str]) -> None:
    """emits: trong manifest chỉ được nêu tên đã đăng ký."""
    for manifest in ROOT.glob("*/*/manifest.yaml"):
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in re.finditer(r"^(emits|listens):\s*\[(?P<items>[^\]]*)\]", text, re.M):
            for raw in block["items"].split(","):
                name = raw.strip().strip("\"'")
                if not name:
                    continue
                if "*" in name:  # pattern subscriber, hợp lệ
                    continue
                if name not in declared:
                    errors.append(
                        f"{manifest.relative_to(ROOT)}: '{name}' chưa khai báo trong EventType"
                    )


def main() -> int:
    declared = declared_event_types()
    schemas = schema_files()

    check_naming(declared)

    for name in sorted(declared):
        if name not in schemas:
            errors.append(
                f"'{name}' đã khai báo trong EventType nhưng thiếu "
                f"schemas/events/{name}.v1.schema.json"
            )

    for name in sorted(schemas):
        if name not in declared:
            errors.append(
                f"schemas/events/{name}.*.schema.json là schema mồ côi — "
                "không có thành viên EventType nào tương ứng"
            )

    check_manifests(declared)

    for w in warnings:
        print(f"⚠  {w}")
    for e in errors:
        print(f"✗  {e}")

    if errors:
        print(f"\n✗ Cổng 5 đỏ: {len(errors)} vấn đề. Event Schema là hợp đồng dài hạn (P10).")
        return 1

    print(f"✓ Cổng 5: {len(declared)} event, tất cả đều có schema đã đăng ký")
    return 0


if __name__ == "__main__":
    sys.exit(main())
