"""Sinh ID dạng <tiền_tố>_<ULID>, đơn điệu trong cùng millisecond (ADR-0023)."""

from __future__ import annotations

import ulid

from kernel import clock

_generator = ulid.ULIDGenerator(policy=ulid.StrictMonotonicPolicy())


def new_id(prefix: str) -> str:
    """Sinh một ID mới. `prefix` là tiền tố loại thực thể, vd 'job', 'proc', 'task', 'evt'."""
    value = _generator.generate(timestamp=clock.now())
    return f"{prefix}_{value}"
