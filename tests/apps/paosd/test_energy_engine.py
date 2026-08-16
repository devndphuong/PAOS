"""Kiểm EnergyEngine — CPU/pin/nhiệt thật (snapshot giả điều khiển được), hot
reload policies/energy.yaml (doc 06 §4, doc 19 P-M7-2, ADR-0030)."""

from __future__ import annotations

from pathlib import Path

import yaml

from apps.paosd.energy_engine import EnergyEngine, MachineSnapshot


def _write_energy_policy(path: Path, content: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, **content}), encoding="utf-8")


def _snapshot(*, cpu_load: float = 0.1, on_battery: bool = False, temp_c: float | None = None):
    def _fn() -> MachineSnapshot:
        return MachineSnapshot(cpu_load=cpu_load, on_battery=on_battery, temp_c=temp_c)

    return _fn


def test_check_allows_when_no_policy_file(tmp_path: Path) -> None:
    engine = EnergyEngine(tmp_path / "khong-ton-tai.yaml", _snapshot(cpu_load=99.0))
    check = engine.check("text.generate@1")
    assert check.allowed is True  # thiếu policy -> không giả vờ có ngưỡng để chặn


def test_check_blocks_when_cpu_overloaded(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(path, {"cpu": {"max_load_to_start": 0.75}})
    engine = EnergyEngine(path, _snapshot(cpu_load=0.9))

    check = engine.check("text.generate@1")
    assert check.allowed is False
    assert check.reason is not None
    assert check.reason.startswith("CPU_OVERLOADED")


def test_check_allows_when_cpu_under_threshold(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(path, {"cpu": {"max_load_to_start": 0.75}})
    engine = EnergyEngine(path, _snapshot(cpu_load=0.5))

    assert engine.check("text.generate@1").allowed is True


def test_check_defers_heavy_capability_on_battery(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(
        path, {"battery": {"on_battery": {"defer_heavy": True, "allow": ["text.generate@1"]}}}
    )
    engine = EnergyEngine(path, _snapshot(on_battery=True))

    assert engine.check("text.generate@1").allowed is True  # trong allowlist
    heavy_check = engine.check("image.generate@1")
    assert heavy_check.allowed is False
    assert heavy_check.reason == "ON_BATTERY_DEFER_HEAVY"


def test_check_ignores_battery_rule_when_plugged_in(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(
        path, {"battery": {"on_battery": {"defer_heavy": True, "allow": ["text.generate@1"]}}}
    )
    engine = EnergyEngine(path, _snapshot(on_battery=False))

    assert engine.check("image.generate@1").allowed is True


def test_check_blocks_on_thermal_throttle(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(path, {"thermal": {"throttle_above_c": 85}})
    engine = EnergyEngine(path, _snapshot(temp_c=90.0))

    check = engine.check("text.generate@1")
    assert check.allowed is False
    assert check.reason is not None
    assert check.reason.startswith("THERMAL_THROTTLE")


def test_check_does_not_block_when_temp_unavailable(tmp_path: Path) -> None:
    """Windows: psutil.sensors_temperatures() không khả dụng -> temp_c=None
    (ADR-0030) -> KHÔNG chặn vì thiếu dữ liệu, không giả vờ có số."""
    path = tmp_path / "energy.yaml"
    _write_energy_policy(path, {"thermal": {"throttle_above_c": 85}})
    engine = EnergyEngine(path, _snapshot(temp_c=None))

    assert engine.check("text.generate@1").allowed is True


def test_check_hot_reload(tmp_path: Path) -> None:
    path = tmp_path / "energy.yaml"
    _write_energy_policy(path, {"cpu": {"max_load_to_start": 0.95}})
    engine = EnergyEngine(path, _snapshot(cpu_load=0.8))

    assert engine.check("text.generate@1").allowed is True

    _write_energy_policy(path, {"cpu": {"max_load_to_start": 0.5}})
    assert engine.check("text.generate@1").allowed is False  # hiệu lực NGAY, không dựng lại engine
