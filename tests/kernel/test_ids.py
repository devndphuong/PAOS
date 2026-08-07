"""Kiểm kernel.ids — ULID có tiền tố, đơn điệu (ADR-0023)."""

from datetime import UTC, datetime

from kernel import clock, ids


def test_new_id_has_correct_prefix() -> None:
    value = ids.new_id("job")
    assert value.startswith("job_")
    assert len(value) == len("job_") + 26  # ULID chuẩn = 26 ký tự base32


def test_ids_are_sortable_by_creation_order() -> None:
    a = ids.new_id("evt")
    b = ids.new_id("evt")
    assert a < b


def test_new_id_uses_injected_clock_and_stays_monotonic_same_millisecond() -> None:
    fixed = datetime(2026, 1, 1, tzinfo=UTC)

    class FixedClock:
        def now(self) -> datetime:
            return fixed

    original = clock.get_clock()
    try:
        clock.set_clock(FixedClock())
        a = ids.new_id("proc")
        b = ids.new_id("proc")
        assert a != b
        assert a < b  # StrictMonotonicPolicy đảm bảo thứ tự dù cùng millisecond
    finally:
        clock.set_clock(original)
