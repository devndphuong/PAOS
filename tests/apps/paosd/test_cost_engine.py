"""Kiểm CostEngine — record_actual() từ usage thật, check_budget() 3 tầng, hot
reload policies/budget.yaml (doc 06 §3, doc 19 P-M7-1); record_savings() +
monthly_report() (doc 06 §3.4, doc 19 P-M7-3, ADR-0031)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from apps.paosd.cost_engine import CostEngine, compute_actual_amount
from kernel import clock
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


def _write_budget_policy(
    path: Path, budgets: dict[str, dict[str, object]], warn_at_pct: float = 80
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"version": 1, "budgets": budgets, "warn_at_pct": warn_at_pct}),
        encoding="utf-8",
    )


def test_compute_actual_amount_from_token_usage() -> None:
    manifest_cost = {"unit": "token", "in": 0.0012, "out": 0.0048, "currency": "JPY"}
    amount, qty = compute_actual_amount(manifest_cost, {"in_tokens": 100, "out_tokens": 50})
    assert amount == pytest.approx(100 * 0.0012 + 50 * 0.0048)
    assert qty == 150


def test_compute_actual_amount_second_unit_is_always_zero() -> None:
    """doc 06 §3.1 — local "điện tính riêng", CostEngine cố tình không đo."""
    amount, qty = compute_actual_amount({"unit": "second", "rate": 5}, {})
    assert amount == 0.0
    assert qty == 0.0


def test_compute_actual_amount_missing_usage_defaults_to_zero() -> None:
    amount, qty = compute_actual_amount({"unit": "token", "in": 1.0, "out": 1.0}, {})
    assert amount == 0.0
    assert qty == 0.0


async def test_record_actual_writes_cost_entry(store: StateStore) -> None:
    engine = CostEngine(store)
    await engine.record_actual(
        process_id="proc_1",
        task_id=None,
        provider_id="provider.x",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 0.001, "out": 0.002, "currency": "JPY"},
        usage={"in_tokens": 10, "out_tokens": 20},
    )
    check = await engine.check_budget("proc_1", 0.0)
    assert check.allowed is True  # sanity — không raise, không cần budget.yaml thật


async def test_check_budget_allows_when_no_policy_file(store: StateStore, tmp_path: Path) -> None:
    engine = CostEngine(store, tmp_path / "khong-ton-tai.yaml")
    check = await engine.check_budget("proc_1", 999999.0)
    assert check.allowed is True  # thiếu policy -> không giả vờ có ngân sách để chặn


async def test_check_budget_per_job_ask_blocks(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    _write_budget_policy(path, {"per_job": {"max": 10, "currency": "JPY", "on_exceed": "ask"}})
    engine = CostEngine(store, path)

    await engine.record_actual(
        process_id="proc_1",
        task_id=None,
        provider_id="provider.x",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
        usage={"in_tokens": 9, "out_tokens": 0},
    )
    check = await engine.check_budget("proc_1", 2.0)  # 9 + 2 = 11 > 10
    assert check.allowed is False
    assert check.action == "ask"
    assert check.tier_name == "per_job"


async def test_check_budget_per_job_scoped_to_process(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    _write_budget_policy(path, {"per_job": {"max": 10, "currency": "JPY", "on_exceed": "ask"}})
    engine = CostEngine(store, path)

    await engine.record_actual(
        process_id="proc_1",
        task_id=None,
        provider_id="provider.x",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
        usage={"in_tokens": 9, "out_tokens": 0},
    )
    check = await engine.check_budget("proc_2", 5.0)  # process khác — không cộng dồn
    assert check.allowed is True


async def test_check_budget_per_day_force_local(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    _write_budget_policy(
        path, {"per_day": {"max": 100, "currency": "JPY", "on_exceed": "force_local"}}
    )
    engine = CostEngine(store, path)
    await engine.record_actual(
        process_id="proc_1",
        task_id=None,
        provider_id="provider.x",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
        usage={"in_tokens": 95, "out_tokens": 0},
    )
    check = await engine.check_budget(
        "proc_2", 10.0
    )  # 95 + 10 = 105 > 100, khác process nhưng cùng ngày
    assert check.allowed is False
    assert check.action == "force_local"
    assert check.tier_name == "per_day"


async def test_check_budget_hot_reload(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    _write_budget_policy(path, {"per_job": {"max": 1000, "currency": "JPY", "on_exceed": "ask"}})
    engine = CostEngine(store, path)

    assert (await engine.check_budget("proc_1", 5.0)).allowed is True

    _write_budget_policy(path, {"per_job": {"max": 1, "currency": "JPY", "on_exceed": "ask"}})
    check = await engine.check_budget("proc_1", 5.0)
    assert check.allowed is False  # đổi file -> hiệu lực NGAY, không cần dựng lại CostEngine


async def test_check_budget_day_used_pct_computed(store: StateStore, tmp_path: Path) -> None:
    path = tmp_path / "budget.yaml"
    _write_budget_policy(path, {"per_day": {"max": 100, "currency": "JPY", "on_exceed": "ask"}})
    engine = CostEngine(store, path)
    await engine.record_actual(
        process_id="proc_1",
        task_id=None,
        provider_id="provider.x",
        capability="text.generate@1",
        manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
        usage={"in_tokens": 40, "out_tokens": 0},
    )
    check = await engine.check_budget("proc_1", 0.0)
    assert check.day_used_pct == pytest.approx(40.0)


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


async def test_monthly_report_sums_spent_and_savings_by_kind(store: StateStore) -> None:
    engine = CostEngine(store)
    old = clock.get_clock()
    clock.set_clock(_FixedClock(datetime(2026, 8, 15, tzinfo=UTC)))
    try:
        await engine.record_actual(
            process_id="proc_1",
            task_id=None,
            provider_id="provider.x",
            capability="text.generate@1",
            manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
            usage={"in_tokens": 100, "out_tokens": 0},
        )
        await engine.record_savings(
            process_id="proc_1",
            task_id=None,
            capability="text.generate@1",
            kind="cache",
            avoided_provider_id="provider.x",
            amount=30.0,
            currency="JPY",
        )
        await engine.record_savings(
            process_id="proc_1",
            task_id=None,
            capability="text.generate@1",
            kind="local",
            avoided_provider_id="provider.cloud",
            amount=50.0,
            currency="JPY",
        )
    finally:
        clock.set_clock(old)

    report = await engine.monthly_report("2026-08")
    assert report.total_spent == pytest.approx(100.0)
    assert report.saved_cache == pytest.approx(30.0)
    assert report.saved_local == pytest.approx(50.0)


async def test_monthly_report_excludes_entries_outside_month(store: StateStore) -> None:
    engine = CostEngine(store)
    old = clock.get_clock()
    clock.set_clock(_FixedClock(datetime(2026, 7, 31, 23, 0, tzinfo=UTC)))
    try:
        await engine.record_actual(
            process_id="proc_1",
            task_id=None,
            provider_id="provider.x",
            capability="text.generate@1",
            manifest_cost={"unit": "token", "in": 1.0, "out": 0.0, "currency": "JPY"},
            usage={"in_tokens": 100, "out_tokens": 0},
        )
    finally:
        clock.set_clock(old)

    report = await engine.monthly_report("2026-08")
    assert report.total_spent == pytest.approx(0.0)  # tháng 7, không tính vào báo cáo tháng 8


async def test_monthly_report_zero_when_no_entries(store: StateStore) -> None:
    engine = CostEngine(store)
    report = await engine.monthly_report("2026-01")
    assert report.total_spent == 0.0
    assert report.saved_cache == 0.0
    assert report.saved_local == 0.0
    assert report.currency == "JPY"
