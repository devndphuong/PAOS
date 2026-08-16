"""Kiểm sdk/preference.py (doc 07 §2.1, P-M5-2) — engine thuần logic."""

import pytest

from sdk.preference import (
    CONFIDENCE_DELTA,
    INITIAL_CANDIDATE_CONFIDENCE,
    ObservationKind,
    PromotionAction,
    apply_delta,
    decide_promotion,
)


class TestApplyDelta:
    def test_explicit_confirm_adds_0_25(self) -> None:
        assert apply_delta(0.5, ObservationKind.EXPLICIT_CONFIRM) == pytest.approx(0.75)

    def test_silent_accept_adds_0_10(self) -> None:
        assert apply_delta(0.5, ObservationKind.SILENT_ACCEPT) == pytest.approx(0.6)

    def test_corrected_subtracts_0_40(self) -> None:
        assert apply_delta(0.7, ObservationKind.CORRECTED) == pytest.approx(0.3)

    def test_contradicted_subtracts_0_15(self) -> None:
        assert apply_delta(0.5, ObservationKind.CONTRADICTED) == pytest.approx(0.35)

    def test_clamps_at_upper_bound_1(self) -> None:
        assert apply_delta(0.9, ObservationKind.EXPLICIT_CONFIRM) == pytest.approx(1.0)

    def test_clamps_at_lower_bound_0(self) -> None:
        assert apply_delta(0.1, ObservationKind.CORRECTED) == pytest.approx(0.0)

    def test_all_kinds_have_a_delta_defined(self) -> None:
        for kind in ObservationKind:
            assert kind in CONFIDENCE_DELTA


class TestDecidePromotion:
    def test_at_or_above_0_75_is_auto_apply(self) -> None:
        assert decide_promotion(0.75) == PromotionAction.AUTO_APPLY
        assert decide_promotion(1.0) == PromotionAction.AUTO_APPLY

    def test_between_0_4_and_0_75_is_suggest(self) -> None:
        assert decide_promotion(0.4) == PromotionAction.SUGGEST
        assert decide_promotion(0.6) == PromotionAction.SUGGEST
        assert decide_promotion(0.749) == PromotionAction.SUGGEST

    def test_below_0_4_is_ignore(self) -> None:
        assert decide_promotion(0.0) == PromotionAction.IGNORE
        assert decide_promotion(0.399) == PromotionAction.IGNORE

    def test_brand_new_candidate_alone_is_ignored(self) -> None:
        """doc 07 §2 — vừa tạo ứng viên (chưa có lần củng cố nào) chưa đủ để gợi ý."""
        assert decide_promotion(INITIAL_CANDIDATE_CONFIDENCE) == PromotionAction.IGNORE

    def test_one_reinforcement_reaches_suggest(self) -> None:
        """Tạo ứng viên (quan sát 1) + 1 lần củng cố (quan sát 2) = đủ để gợi ý."""
        confidence = apply_delta(INITIAL_CANDIDATE_CONFIDENCE, ObservationKind.SILENT_ACCEPT)
        assert decide_promotion(confidence) == PromotionAction.SUGGEST

    def test_several_reinforcements_reach_auto_apply(self) -> None:
        confidence = INITIAL_CANDIDATE_CONFIDENCE
        for _ in range(5):
            confidence = apply_delta(confidence, ObservationKind.EXPLICIT_CONFIRM)
        assert decide_promotion(confidence) == PromotionAction.AUTO_APPLY
