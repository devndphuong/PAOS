"""EnergyEngine — đọc trạng thái máy THẬT (CPU/RAM/pin, không phải chỉ đếm
Task nội bộ paosd) trước khi cấp resource token (doc 06 §4, doc 19 P-M7-2,
ADR-0030).

Cùng khuôn `cost_engine.py`: DAO/query thuần, không side-effect, không tự phát
Event (caller — `Router` — tự publish theo kết quả `check()`).

`policies/energy.yaml` đọc lại MỖI LẦN gọi — hot reload, cùng tiền lệ
`policies/{intents,routing,budget}.yaml`.

Chỉ chặn dựa trên tín hiệu THẬT SỰ đọc được (ADR-0030): thiếu 1 sensor (nhiệt
độ trên Windows, GPU luôn — chưa đọc ở lát này) không được phép chặn oan —
số hạng đó đơn giản vắng mặt khỏi quyết định, không giả vờ bằng giá trị an
toàn/nguy hiểm bịa ra."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil
import yaml

_DEFAULT_ENERGY_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "energy.yaml"


@dataclass(frozen=True)
class MachineSnapshot:
    cpu_load: float  # psutil.cpu_percent()/100 — 0.0-1.0+, cùng thang với max_load_to_start
    on_battery: bool
    temp_c: float | None  # None nếu nền tảng không hỗ trợ (Windows — ADR-0030)


def read_machine_snapshot() -> MachineSnapshot:
    """`cpu_percent(interval=None)` KHÔNG chặn — đo delta kể từ lần gọi trước
    (cần "mồi" 1 lần lúc khởi động, xem `EnergyEngine.__init__`, ADR-0030)."""
    cpu_load = psutil.cpu_percent(interval=None) / 100.0
    battery = psutil.sensors_battery()
    on_battery = battery is not None and not battery.power_plugged
    return MachineSnapshot(cpu_load=cpu_load, on_battery=on_battery, temp_c=_read_max_temp_c())


def _read_max_temp_c() -> float | None:
    try:
        # sensors_temperatures() chỉ có trên Linux — stub types-psutil không khai
        # báo cho mọi nền tảng, cùng lý do runtime AttributeError bên dưới (ADR-0030).
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
    except AttributeError:
        return None  # Windows — ADR-0030, không phải lỗi, chỉ là không có driver
    all_readings = [entry.current for entries in temps.values() for entry in entries]
    return max(all_readings) if all_readings else None


@dataclass(frozen=True)
class _CpuTier:
    max_load_to_start: float


@dataclass(frozen=True)
class _BatteryTier:
    defer_heavy: bool
    allow: frozenset[str]


@dataclass(frozen=True)
class _ThermalTier:
    throttle_above_c: float


@dataclass(frozen=True)
class EnergyPolicy:
    cpu: _CpuTier | None
    battery: _BatteryTier | None
    thermal: _ThermalTier | None


@dataclass(frozen=True)
class EnergyCheck:
    allowed: bool
    reason: str | None


def _load_energy_policy(path: Path) -> EnergyPolicy | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cpu_raw = data.get("cpu")
    cpu = _CpuTier(max_load_to_start=float(cpu_raw["max_load_to_start"])) if cpu_raw else None

    battery_raw = (data.get("battery") or {}).get("on_battery")
    battery = (
        _BatteryTier(
            defer_heavy=bool(battery_raw.get("defer_heavy", False)),
            allow=frozenset(battery_raw.get("allow", [])),
        )
        if battery_raw
        else None
    )

    thermal_raw = data.get("thermal")
    thermal = (
        _ThermalTier(throttle_above_c=float(thermal_raw["throttle_above_c"]))
        if thermal_raw
        else None
    )

    return EnergyPolicy(cpu=cpu, battery=battery, thermal=thermal)


class EnergyEngine:
    def __init__(
        self,
        energy_policy_path: Path = _DEFAULT_ENERGY_POLICY_PATH,
        snapshot_fn: Callable[[], MachineSnapshot] = read_machine_snapshot,
    ) -> None:
        """`snapshot_fn` injectable — test truyền snapshot giả điều khiển
        được, KHÔNG phụ thuộc tải máy thật lúc chạy CI (cùng tinh thần
        `kernel/clock.py::set_clock()` cho thời gian)."""
        self._energy_policy_path = energy_policy_path
        self._snapshot_fn = snapshot_fn
        psutil.cpu_percent(interval=None)  # "mồi" — xem docstring read_machine_snapshot()

    def check(self, capability_ref: str) -> EnergyCheck:
        policy = _load_energy_policy(self._energy_policy_path)
        if policy is None:
            return EnergyCheck(allowed=True, reason=None)
        snapshot = self._snapshot_fn()

        if policy.cpu is not None and snapshot.cpu_load > policy.cpu.max_load_to_start:
            return EnergyCheck(allowed=False, reason=f"CPU_OVERLOADED:{snapshot.cpu_load:.2f}")
        if (
            policy.battery is not None
            and policy.battery.defer_heavy
            and snapshot.on_battery
            and capability_ref not in policy.battery.allow
        ):
            return EnergyCheck(allowed=False, reason="ON_BATTERY_DEFER_HEAVY")
        if (
            policy.thermal is not None
            and snapshot.temp_c is not None
            and snapshot.temp_c > policy.thermal.throttle_above_c
        ):
            return EnergyCheck(allowed=False, reason=f"THERMAL_THROTTLE:{snapshot.temp_c:.1f}")
        return EnergyCheck(allowed=True, reason=None)
