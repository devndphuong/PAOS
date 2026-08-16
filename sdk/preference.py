"""Bộ máy tính `confidence` cho sở thích học được (doc 07 §2.1, P-M5-2).

Thuần logic, không I/O — cùng triết lý `sdk/rubric.py`/`sdk/eval.py`: công
thức áp dụng được cả ở `apps/paosd/memory_writer.py` (viết thời gian thực,
theo Event) lẫn một job hàng loạt sau này nếu cần, không gắn với đường đi cụ
thể nào.

Doc 07 §2.1 cho 3 mức δ (xác nhận rõ ràng, chấp nhận mặc định im lặng, sửa
tay) và 3 ngưỡng hành động — nhưng KHÔNG nói gì về trường hợp một giá trị
MỚI mâu thuẫn với giá trị đã quan sát trước đó (doc 07 §2 chỉ nói chung
chung "mâu thuẫn với quan sát mới → Xét lại → hạ tin cậy"). Quyết định của
module này: `CONTRADICTED` dùng δ nhẹ hơn `CORRECTED` (-0.15 so với -0.40)
— mâu thuẫn giữa 2 lần QUAN SÁT THỤ ĐỘNG đáng ngờ hơn 1 lần đồng thuận, nhưng
KHÔNG chắc chắn bằng việc người dùng chủ động sửa tay kết quả (`CORRECTED`,
tín hiệu khách quan nhất theo doc 08 §5)."""

from __future__ import annotations

from enum import StrEnum

_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 1.0

# Ngưỡng hành động, doc 07 §2.1: "Áp dụng tự động khi confidence >= 0.75; chỉ
# gợi ý khi 0.4-0.75; bỏ qua khi < 0.4".
_AUTO_APPLY_THRESHOLD = 0.75
_SUGGEST_THRESHOLD = 0.4


class ObservationKind(StrEnum):
    """Loại quan sát dẫn tới thay đổi `confidence` (doc 07 §2.1)."""

    EXPLICIT_CONFIRM = "explicit_confirm"  # người dùng xác nhận rõ ràng — δ=+0.25
    SILENT_ACCEPT = "silent_accept"  # chấp nhận mặc định (im lặng) / lặp lại quan sát — δ=+0.10
    CORRECTED = "corrected"  # người dùng sửa tay kết quả (doc 08 §5, edit_rate) — δ=-0.40
    CONTRADICTED = "contradicted"  # quan sát mới mâu thuẫn giá trị cũ — δ=-0.15 (xem docstring)


CONFIDENCE_DELTA: dict[ObservationKind, float] = {
    ObservationKind.EXPLICIT_CONFIRM: 0.25,
    ObservationKind.SILENT_ACCEPT: 0.10,
    ObservationKind.CORRECTED: -0.40,
    ObservationKind.CONTRADICTED: -0.15,
}

# confidence khởi đầu cho 1 ứng viên MỚI (chưa từng quan sát) — doc 07 §2 không
# ghi con số, nhưng nói "chỉ gợi ý khi 0.4-0.75" — 0.3 (dưới 0.4, bị "bỏ qua")
# đảm bảo 1 lần quan sát duy nhất KHÔNG tự đủ để gợi ý, cần ít nhất 1 lần củng
# cố nữa (đúng tinh thần "lặp lại >= N lần" của doc 07 §2).
INITIAL_CANDIDATE_CONFIDENCE = 0.3


def apply_delta(confidence: float, kind: ObservationKind) -> float:
    """`confidence_mới = clamp(confidence + δ)` — doc 07 §2.1, nguyên văn."""
    updated = confidence + CONFIDENCE_DELTA[kind]
    return max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, updated))


class PromotionAction(StrEnum):
    AUTO_APPLY = "auto_apply"
    SUGGEST = "suggest"
    IGNORE = "ignore"


def decide_promotion(confidence: float) -> PromotionAction:
    if confidence >= _AUTO_APPLY_THRESHOLD:
        return PromotionAction.AUTO_APPLY
    if confidence >= _SUGGEST_THRESHOLD:
        return PromotionAction.SUGGEST
    return PromotionAction.IGNORE
