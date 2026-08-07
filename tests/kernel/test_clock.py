"""Kiểm kernel.clock — nguồn thời gian injectable (D-02)."""

from datetime import UTC, datetime

from kernel import clock


def test_system_clock_returns_timezone_aware_utc() -> None:
    now = clock.now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


def test_set_clock_overrides_for_deterministic_tests() -> None:
    fixed = datetime(2026, 1, 1, tzinfo=UTC)

    class FixedClock:
        def now(self) -> datetime:
            return fixed

    original = clock.get_clock()
    try:
        clock.set_clock(FixedClock())
        assert clock.now() == fixed
    finally:
        clock.set_clock(original)
