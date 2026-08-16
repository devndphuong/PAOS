"""Kiểm TimeEngine — cửa sổ thời gian, hot reload policies/time.yaml (doc 06
§5, doc 19 P-M7-3, ADR-0031)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from apps.paosd.time_engine import TimeEngine

# 2026-08-17 = thứ 2 (mon).
_MON_10AM = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
_MON_10PM = datetime(2026, 8, 17, 22, 0, tzinfo=UTC)
_SAT_10AM = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
_MON_3AM = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)


def _write_time_policy(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, **content}), encoding="utf-8")


def test_check_allows_when_no_policy_file(tmp_path: Path) -> None:
    engine = TimeEngine(tmp_path / "khong-ton-tai.yaml")
    check = engine.check("video.render@1", _MON_10AM)
    assert check.allowed is True  # thiếu policy -> không giả vờ có cửa sổ để chặn


def test_check_blocks_heavy_capability_during_work_hours(tmp_path: Path) -> None:
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "work_hours",
                    "days": ["mon", "tue", "wed", "thu", "fri"],
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False, "allow": [], "max_parallel": 1},
                }
            ],
            "default": {"allow_heavy": True, "max_parallel": 2},
        },
    )
    engine = TimeEngine(path)
    check = engine.check("video.render@1", _MON_10AM)
    assert check.allowed is False
    assert check.reason is not None
    assert check.reason.startswith("TIME_WINDOW_BLOCKED:work_hours")
    assert check.window_name == "work_hours"
    assert check.next_window_at == datetime(2026, 8, 17, 18, 0, tzinfo=UTC)


def test_check_allows_allowlisted_capability_during_blocked_window(tmp_path: Path) -> None:
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "work_hours",
                    "days": ["mon"],
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False, "allow": ["text.generate@1"]},
                }
            ],
            "default": {"allow_heavy": True},
        },
    )
    engine = TimeEngine(path)
    assert engine.check("text.generate@1", _MON_10AM).allowed is True
    assert engine.check("video.render@1", _MON_10AM).allowed is False


def test_check_ignores_window_on_non_matching_day(tmp_path: Path) -> None:
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "work_hours",
                    "days": ["mon", "tue", "wed", "thu", "fri"],
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False},
                }
            ],
            "default": {"allow_heavy": True},
        },
    )
    engine = TimeEngine(path)
    assert engine.check("video.render@1", _SAT_10AM).allowed is True  # thứ 7 -> default


def test_check_allows_outside_window_hours(tmp_path: Path) -> None:
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "work_hours",
                    "days": ["mon"],
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False},
                }
            ],
            "default": {"allow_heavy": True},
        },
    )
    engine = TimeEngine(path)
    assert engine.check("video.render@1", _MON_10PM).allowed is True  # default áp dụng


def test_check_window_wraps_midnight(tmp_path: Path) -> None:
    """night 21:00->07:00 (doc 06 §5) — [21:00, 24:00) hợp [00:00, 07:00)."""
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "night",
                    "from": "21:00",
                    "to": "07:00",
                    "policy": {"allow_heavy": True, "max_parallel": 4},
                },
                {
                    "name": "work_hours",
                    "days": ["mon"],
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False},
                },
            ],
            "default": {"allow_heavy": True},
        },
    )
    engine = TimeEngine(path)
    assert engine.check("video.render@1", _MON_3AM).allowed is True
    assert engine.check("video.render@1", _MON_10PM).allowed is True
    assert engine.check("video.render@1", _MON_10AM).allowed is False  # work_hours khớp trước


def test_check_hot_reload(tmp_path: Path) -> None:
    path = tmp_path / "time.yaml"
    _write_time_policy(
        path,
        {
            "windows": [
                {"name": "work_hours", "from": "08:00", "to": "18:00", "policy": {}},
            ],
            "default": {"allow_heavy": True},
        },
    )
    engine = TimeEngine(path)
    assert engine.check("video.render@1", _MON_10AM).allowed is True  # allow_heavy mặc định True

    _write_time_policy(
        path,
        {
            "windows": [
                {
                    "name": "work_hours",
                    "from": "08:00",
                    "to": "18:00",
                    "policy": {"allow_heavy": False},
                },
            ],
            "default": {"allow_heavy": True},
        },
    )
    check = engine.check("video.render@1", _MON_10AM)
    assert check.allowed is False  # hiệu lực NGAY, không cần dựng lại engine
