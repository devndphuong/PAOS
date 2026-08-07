"""Nguồn thời gian duy nhất của Kernel — injectable để test tất định (D-02, doc 18 §3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_clock: Clock = SystemClock()


def now() -> datetime:
    """Giờ hiện tại theo Clock đang cấu hình.

    Không nơi nào khác được gọi datetime.now() trực tiếp (R05).
    """
    return _clock.now()


def set_clock(clock: Clock) -> None:
    """Ghi đè Clock — dùng trong test/PAOS_MODE=deterministic. Không dùng ở code production."""
    global _clock  # noqa: PLW0603 — đúng 1 điểm ghi đè duy nhất, cố ý, cho injectable clock
    _clock = clock


def get_clock() -> Clock:
    """Clock đang được cấu hình — dùng để lưu/khôi phục trong test, không dùng ở code production."""
    return _clock
