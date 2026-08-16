"""TimeEngine — cửa sổ thời gian: giờ này chạy capability "nặng" được không
(doc 06 §5, doc 19 P-M7-3, ADR-0031).

Cùng khuôn `cost_engine.py`/`energy_engine.py`: DAO/quy tắc thuần, không
side-effect, không tự phát Event (caller — `Router` — tự publish theo kết quả
`check()`, cùng lý do `EnergyEngine`).

`policies/time.yaml` đọc lại MỖI LẦN gọi — hot reload, cùng tiền lệ
`policies/{intents,routing,budget,energy}.yaml`.

ADR-0031 — phạm vi CÓ CHỦ Ý của lát này (không giả vờ làm hơn):
- Giờ dùng `kernel.clock.now()` (UTC) TRỰC TIẾP làm giờ khớp cửa sổ — timezone
  local KHÔNG được cấu hình ở đâu trong hệ thống hôm nay (cùng cách
  `CostEngine.check_budget()` tính start_of_day/start_of_month). `policies/
  time.yaml` viết giờ như thể đã là giờ hệ thống chạy; đổi timezone chờ nhu cầu
  thật (chưa 2 ca dùng thật, xem docs/backlog.md).
- `max_parallel` được đọc/parse nhưng KHÔNG được enforce — `Runner` giữ đúng 1
  `asyncio.Semaphore` toàn cục, chưa có cơ chế đổi giới hạn theo cửa sổ đang
  hoạt động (xem docs/backlog.md).
- `deadline` miễn trừ (doc 06 §5 "Job có deadline được miễn trừ") CHƯA làm —
  `Router.call()` chưa có tham số `deadline`, cần luồng JobSpec.constraints.
  deadline → Process → Router mà lát này không đụng tới (xem docs/backlog.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml

_DEFAULT_TIME_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "time.yaml"
_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class _WindowPolicy:
    allow_heavy: bool
    allow: frozenset[str]
    max_parallel: int | None
    notify: str


@dataclass(frozen=True)
class _TimeWindow:
    name: str
    days: frozenset[str] | None  # None = mọi ngày
    from_time: time
    to_time: time
    policy: _WindowPolicy


@dataclass(frozen=True)
class TimePolicy:
    windows: tuple[_TimeWindow, ...]
    default: _WindowPolicy


@dataclass(frozen=True)
class TimeCheck:
    allowed: bool
    reason: str | None
    window_name: str
    next_window_at: datetime | None


def _parse_hhmm(raw: str) -> time:
    hour, _, minute = raw.partition(":")
    return time(hour=int(hour), minute=int(minute))


def _parse_window_policy(raw: dict[str, object]) -> _WindowPolicy:
    return _WindowPolicy(
        allow_heavy=bool(raw.get("allow_heavy", True)),
        allow=frozenset(raw.get("allow", [])),  # type: ignore[arg-type]
        max_parallel=int(raw["max_parallel"]) if raw.get("max_parallel") is not None else None,
        notify=str(raw.get("notify", "silent")),
    )


def _load_time_policy(path: Path) -> TimePolicy | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    windows = tuple(
        _TimeWindow(
            name=w["name"],
            days=frozenset(w["days"]) if w.get("days") else None,
            from_time=_parse_hhmm(w["from"]),
            to_time=_parse_hhmm(w["to"]),
            policy=_parse_window_policy(w.get("policy") or {}),
        )
        for w in data.get("windows") or []
    )
    default = _parse_window_policy(data.get("default") or {})
    return TimePolicy(windows=windows, default=default)


def _time_in_range(now_t: time, start: time, end: time) -> bool:
    """`start > end` nghĩa là cửa sổ vắt qua nửa đêm (vd night 21:00->07:00,
    doc 06 §5) — [start, 24:00) hợp [00:00, end)."""
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end


def _matching_window(windows: tuple[_TimeWindow, ...], now: datetime) -> _TimeWindow | None:
    weekday = _WEEKDAY_NAMES[now.weekday()]
    now_t = now.time()
    for window in windows:
        if window.days is not None and weekday not in window.days:
            continue
        if _time_in_range(now_t, window.from_time, window.to_time):
            return window
    return None


def _window_end_at(window: _TimeWindow, now: datetime) -> datetime:
    """Mốc kết thúc cửa sổ ĐANG khớp `now` — best-effort cho payload event/CLI,
    KHÔNG đảm bảo cửa sổ kế tiếp cũng cho phép capability này (caller vẫn phải
    tự retry, không tin đây là mốc CHẮC CHẮN chạy được, ADR-0031)."""
    end = datetime.combine(now.date(), window.to_time, tzinfo=now.tzinfo)
    if end <= now:
        end += timedelta(days=1)
    return end


class TimeEngine:
    def __init__(self, time_policy_path: Path = _DEFAULT_TIME_POLICY_PATH) -> None:
        self._time_policy_path = time_policy_path

    def check(self, capability_ref: str, now: datetime) -> TimeCheck:
        policy = _load_time_policy(self._time_policy_path)
        if policy is None:
            return TimeCheck(allowed=True, reason=None, window_name="default", next_window_at=None)

        window = _matching_window(policy.windows, now)
        window_policy = window.policy if window is not None else policy.default
        window_name = window.name if window is not None else "default"

        if window_policy.allow_heavy or capability_ref in window_policy.allow:
            return TimeCheck(
                allowed=True, reason=None, window_name=window_name, next_window_at=None
            )

        next_at = _window_end_at(window, now) if window is not None else None
        return TimeCheck(
            allowed=False,
            reason=f"TIME_WINDOW_BLOCKED:{window_name}",
            window_name=window_name,
            next_window_at=next_at,
        )
