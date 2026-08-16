"""CostEngine — estimate() trước khi gọi, ghi cost_entries thật sau khi gọi,
ngân sách 3 tầng (doc 06 §3, doc 19 P-M7-1); ghi nhận `savings_entries` +
báo cáo tháng (doc 06 §3.4, doc 19 P-M7-3, ADR-0031).

Cùng khuôn `apps/paosd/cache_store.py`/`provider_stats.py`: DAO thuần, không tự
phát Event (Router — caller — tự publish/raise theo kết quả `check_budget()`,
cùng lý do CacheStore không tự emit cache_hit).

`policies/budget.yaml` đọc lại MỖI LẦN gọi `check_budget()` — hot reload, cùng
tiền lệ `policies/intents.yaml` (P-M6-1) và `policies/routing.yaml` (P-M6-3).

`record_actual()` — `unit=="token"`: `amount = in_tokens·cost.in + out_tokens·cost.out`
(khớp đúng `output.schema.json::usage` mọi capability text.*). `unit=="second"`
(compute local): `amount = 0` LUÔN — doc 06 §3.1 tự chú thích "local (điện tính
riêng)", đây là quyết định phạm vi CÓ CHỦ ĐÍCH của chính doc, không phải bỏ sót
— CostEngine không đo điện."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import yaml

from kernel import clock
from kernel.state.db import StateStore

_DEFAULT_BUDGET_POLICY_PATH = Path(__file__).resolve().parents[2] / "policies" / "budget.yaml"
_TIER_ORDER = ("per_job", "per_day", "per_month")
_DECEMBER = 12


@dataclass(frozen=True)
class BudgetTier:
    max: float
    currency: str
    on_exceed: str  # "ask" | "force_local" | "block_cloud"


@dataclass(frozen=True)
class BudgetPolicy:
    per_job: BudgetTier | None
    per_day: BudgetTier | None
    per_month: BudgetTier | None
    warn_at_pct: float


@dataclass(frozen=True)
class BudgetCheck:
    """Kết quả `check_budget()`. `allowed=True` -> tiến hành bình thường.
    `allowed=False` -> `action` nói caller phải làm gì (`ask`/`force_local`/
    `block_cloud`), `tier_name` là tầng đầu tiên bị vượt (per_job trước, rồi
    per_day, rồi per_month — tầng hẹp nhất chạm trước luôn ưu tiên báo)."""

    allowed: bool
    action: str | None
    tier_name: str | None
    day_used_pct: float
    warn_at_pct: float


@dataclass(frozen=True)
class MonthlyReport:
    """doc 06 §3.4/doc 13 M7 exit criteria "đã tiêu bao nhiêu, tiết kiệm bao
    nhiêu nhờ local + cache". Giả định 1 currency cho cả báo cáo (JPY, khớp
    mọi `provider.yaml::cost.currency` đã khai trong repo hôm nay — chưa 2 ca
    dùng thật cho đa currency, ADR-0031) thay vì gộp nhầm nhiều đơn vị tiền."""

    year_month: str  # "YYYY-MM"
    currency: str
    total_spent: float
    saved_cache: float
    saved_local: float


def _month_bounds(year_month: str) -> tuple[str, str]:
    year, month = (int(part) for part in year_month.split("-"))
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == _DECEMBER:
        next_month = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=UTC)
    return start.isoformat(), next_month.isoformat()


def _load_budget_policy(path: Path) -> BudgetPolicy | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    budgets = data.get("budgets") or {}

    def _tier(name: str) -> BudgetTier | None:
        raw = budgets.get(name)
        return BudgetTier(**raw) if raw else None

    return BudgetPolicy(
        per_job=_tier("per_job"),
        per_day=_tier("per_day"),
        per_month=_tier("per_month"),
        warn_at_pct=float(data.get("warn_at_pct", 100.0)),
    )


def compute_actual_amount(
    manifest_cost: dict[str, Any], usage: dict[str, Any]
) -> tuple[float, float]:
    """Trả `(amount, qty)`. `qty` = tổng token (đơn vị đo, cho `cost_entries.qty`)."""
    if manifest_cost.get("unit") == "token":
        in_tokens = float(usage.get("in_tokens", 0))
        out_tokens = float(usage.get("out_tokens", 0))
        amount = in_tokens * manifest_cost.get("in", 0) + out_tokens * manifest_cost.get("out", 0)
        return amount, in_tokens + out_tokens
    return 0.0, 0.0


class CostEngine:
    def __init__(
        self, store: StateStore, budget_policy_path: Path = _DEFAULT_BUDGET_POLICY_PATH
    ) -> None:
        self._store = store
        self._budget_policy_path = budget_policy_path

    async def record_actual(
        self,
        *,
        process_id: str | None,
        task_id: str | None,
        provider_id: str,
        capability: str,
        manifest_cost: dict[str, Any],
        usage: dict[str, Any],
    ) -> None:
        amount, qty = compute_actual_amount(manifest_cost, usage)
        unit = manifest_cost.get("unit", "unknown")
        currency = manifest_cost.get("currency", "JPY")

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO cost_entries(process_id, task_id, provider_id, capability, unit, "
                "qty, amount, currency, estimated, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    process_id,
                    task_id,
                    provider_id,
                    capability,
                    unit,
                    qty,
                    amount,
                    currency,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)

    async def record_savings(
        self,
        *,
        process_id: str | None,
        task_id: str | None,
        capability: str,
        kind: str,
        avoided_provider_id: str | None,
        amount: float,
        currency: str,
    ) -> None:
        """`kind`: "cache" (cache hit tránh 1 lượt gọi thật) | "local" (candidate
        local được chọn thay vì candidate cloud/hybrid đủ điều kiện). `amount`
        LUÔN là kết quả `adapter.estimate()` THẬT của `avoided_provider_id` —
        caller (`Router`) chịu trách nhiệm tính, hàm này chỉ ghi (ADR-0031)."""

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO savings_entries(process_id, task_id, capability, kind, "
                "avoided_provider_id, amount, currency, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    process_id,
                    task_id,
                    capability,
                    kind,
                    avoided_provider_id,
                    amount,
                    currency,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)

    async def monthly_report(self, year_month: str, currency: str = "JPY") -> MonthlyReport:
        """doc 13 M7 exit criteria "Báo cáo tháng: đã tiêu bao nhiêu, tiết kiệm
        bao nhiêu nhờ local + cache". `year_month`="YYYY-MM"."""
        start_iso, end_iso = _month_bounds(year_month)

        async def _select(conn: aiosqlite.Connection) -> tuple[float, float, float]:
            spent_cursor = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM cost_entries "
                "WHERE estimated = 0 AND currency = ? AND at >= ? AND at < ?",
                (currency, start_iso, end_iso),
            )
            spent_row = await spent_cursor.fetchone()
            spent = float(spent_row[0]) if spent_row else 0.0

            savings_cursor = await conn.execute(
                "SELECT kind, COALESCE(SUM(amount), 0) FROM savings_entries "
                "WHERE currency = ? AND at >= ? AND at < ? GROUP BY kind",
                (currency, start_iso, end_iso),
            )
            savings_rows = await savings_cursor.fetchall()
            saved_by_kind = {row[0]: float(row[1]) for row in savings_rows}
            return spent, saved_by_kind.get("cache", 0.0), saved_by_kind.get("local", 0.0)

        spent, saved_cache, saved_local = await self._store.read(_select)
        return MonthlyReport(
            year_month=year_month,
            currency=currency,
            total_spent=spent,
            saved_cache=saved_cache,
            saved_local=saved_local,
        )

    async def check_budget(self, process_id: str, estimated_cost: float) -> BudgetCheck:
        """doc 06 §3.2 mốc 2 — kiểm TRƯỚC 1 lượt gọi cụ thể, không phải ước
        lượng tổng cả Job (mốc 1, chưa làm — cần duyệt DAG với input các bước
        sau CHƯA resolve, xem docstring module `apps/paosd/router.py`)."""
        policy = _load_budget_policy(self._budget_policy_path)
        if policy is None:
            return BudgetCheck(
                allowed=True, action=None, tier_name=None, day_used_pct=0.0, warn_at_pct=100.0
            )

        now = clock.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

        job_used = await self._sum_for_process(process_id)
        day_used = await self._sum_since(start_of_day)
        month_used = await self._sum_since(start_of_month)
        used_by_tier = {"per_job": job_used, "per_day": day_used, "per_month": month_used}

        day_pct = (
            (day_used / policy.per_day.max * 100) if policy.per_day and policy.per_day.max else 0.0
        )

        for tier_name in _TIER_ORDER:
            tier: BudgetTier | None = getattr(policy, tier_name)
            if tier is None:
                continue
            if used_by_tier[tier_name] + estimated_cost > tier.max:
                return BudgetCheck(
                    allowed=False,
                    action=tier.on_exceed,
                    tier_name=tier_name,
                    day_used_pct=day_pct,
                    warn_at_pct=policy.warn_at_pct,
                )
        return BudgetCheck(
            allowed=True,
            action=None,
            tier_name=None,
            day_used_pct=day_pct,
            warn_at_pct=policy.warn_at_pct,
        )

    async def _sum_for_process(self, process_id: str) -> float:
        async def _select(conn: aiosqlite.Connection) -> float:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM cost_entries WHERE process_id = ?",
                (process_id,),
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0

        return await self._store.read(_select)

    async def _sum_since(self, since_iso: str) -> float:
        async def _select(conn: aiosqlite.Connection) -> float:
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM cost_entries WHERE at >= ?", (since_iso,)
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0

        return await self._store.read(_select)
