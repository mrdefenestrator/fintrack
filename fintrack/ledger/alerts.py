"""Unusual spending alerts (issue #101): pure checks over transaction rows.

Three local checks, each tuned in ``configs/alerts.yaml`` (AlertConfig):

1. **Large charge** — a charge well above that merchant's median earlier
   charge; with too little merchant history, well above the category's.
2. **New merchant** — the first-ever charge from a merchant, above a small
   amount, once the snapshot has enough history that "first" means something.
3. **Running hot** — part-way through the month, a category's spending so far
   is on pace to land well over its budget for the month (or its recent
   monthly average when it has no budget). "On pace" allows for lump sums:
   like Budget vs Actual, a budgeted category's continuous (variable) lines
   are expected to accrue through the month and its fixed lines in full; an
   unbudgeted one is compared with what it had usually spent by the same day.
   Charges that already carry their own alert don't count, so one big charge
   isn't reported twice.

Spending categories only (Income and Transfer are left out). Charge checks
look at the last ``window_days`` days, so old alerts drop off on their own; a
transaction carries at most one alert (large charge wins over new merchant).
Dismissals are applied by the caller (fintrack.insights.alerts).

Everything here is local: no transaction data goes to the Claude API.
"""

from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal, TypedDict

import yaml

from fintrack.core.config import ALERTS_CONFIG
from fintrack.core.formatting import fmt_day_ordinal, fmt_money_whole

MonthKey = tuple[int, int]
AlertKind = Literal["large_charge", "new_merchant", "running_hot"]


@dataclass(frozen=True)
class AlertConfig:
    """Alert thresholds; configs/alerts.yaml overrides any of these."""

    window_days: int = 30
    excluded_categories: tuple[str, ...] = ("Income", "Transfer")
    large_min_history: int = 3
    large_merchant_multiple: float = 2.0
    large_merchant_min_over: float = 50
    large_category_multiple: float = 3.0
    large_category_min_over: float = 100
    new_merchant_min_amount: float = 50
    new_merchant_min_history_days: int = 60
    hot_start_day: int = 7
    hot_pace_multiple: float = 1.3
    hot_min_over: float = 100
    hot_average_months: int = 6
    hot_min_average_months: int = 3


def load_alert_config(path: Path | str = ALERTS_CONFIG) -> AlertConfig:
    """AlertConfig from YAML; a missing file means all defaults. Unknown keys
    are an error, so a typo doesn't silently fall back to a default."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return AlertConfig()
    known = {f.name for f in fields(AlertConfig)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown alert settings in {path}: {sorted(unknown)}")
    if "excluded_categories" in raw:
        raw["excluded_categories"] = tuple(raw["excluded_categories"] or ())
    return AlertConfig(**raw)


class Alert(TypedDict):
    kind: AlertKind
    # The flagged transaction (charge alerts) or None (running hot).
    transaction_id: int | None
    category: str
    # Charge date, or the 1st of the month for running hot.
    date: date
    merchant: str | None
    # The charge (positive dollars), or month-to-date spending.
    amount: float
    # What it's compared against: the median charge, or the month's
    # budget / average.
    baseline: float
    # "merchant" / "category" (large charge), "budget" / "average" (running
    # hot), None (new merchant).
    basis: str | None
    message: str


def _charge(row: Mapping[str, Any]) -> float:
    """A charge as positive dollars (0 for credits)."""
    amount = float(row["amount"])
    return -amount if amount < 0 else 0.0


def _sort_key(row: Mapping[str, Any]) -> tuple[date, int]:
    return (row["date"], row["id"])


def charge_alerts(
    rows: Iterable[Mapping[str, Any]],
    today: date,
    config: AlertConfig | None = None,
) -> list[Alert]:
    """Large-charge and new-merchant alerts.

    ``rows`` is the snapshot's whole confirmed history (dicts with id, date,
    amount, merchant, category), hidden duplicates already left out. Charges in
    the window are checked against the rows before them. Newest first."""
    cfg = config or AlertConfig()
    excluded = set(cfg.excluded_categories)
    history = sorted((r for r in rows if r["category"] not in excluded), key=_sort_key)
    if not history:
        return []
    first_date = history[0]["date"]
    window_start = today - timedelta(days=cfg.window_days - 1)

    seen_merchants: set[str] = set()
    merchant_charges: dict[str, list[float]] = defaultdict(list)
    category_charges: dict[str, list[float]] = defaultdict(list)
    alerts: list[Alert] = []
    for row in history:
        charge = _charge(row)
        merchant, category = row["merchant"], row["category"]
        if charge > 0 and window_start <= row["date"] <= today:
            alert = _large_charge(
                row, charge, merchant_charges[merchant], category_charges, cfg
            ) or _new_merchant(row, charge, merchant in seen_merchants, first_date, cfg)
            if alert:
                alerts.append(alert)
        seen_merchants.add(merchant)
        if charge > 0:
            merchant_charges[merchant].append(charge)
            category_charges[category].append(charge)
    alerts.sort(key=lambda a: (a["date"], a["transaction_id"] or 0), reverse=True)
    return alerts


def _large_charge(
    row: Mapping[str, Any],
    charge: float,
    merchant_history: list[float],
    category_history: Mapping[str, list[float]],
    cfg: AlertConfig,
) -> Alert | None:
    if len(merchant_history) >= cfg.large_min_history:
        basis, earlier = "merchant", merchant_history
        multiple, min_over = cfg.large_merchant_multiple, cfg.large_merchant_min_over
    else:
        earlier = category_history.get(row["category"], [])
        if len(earlier) < cfg.large_min_history:
            return None
        basis = "category"
        multiple, min_over = cfg.large_category_multiple, cfg.large_category_min_over
    typical = median(earlier)
    if charge <= typical * multiple or charge - typical < min_over:
        return None
    usual = (
        f"usually {fmt_money_whole(typical)}"
        if basis == "merchant"
        else f"typical {row['category']} charge {fmt_money_whole(typical)}"
    )
    return {
        "kind": "large_charge",
        "transaction_id": row["id"],
        "category": row["category"],
        "date": row["date"],
        "merchant": row["merchant"],
        "amount": charge,
        "baseline": typical,
        "basis": basis,
        "message": (
            f"Large charge: {row['merchant']} {fmt_money_whole(charge)} "
            f"on {row['date']:%b} {row['date'].day} ({usual})"
        ),
    }


def _new_merchant(
    row: Mapping[str, Any],
    charge: float,
    seen: bool,
    first_date: date,
    cfg: AlertConfig,
) -> Alert | None:
    if seen or charge < cfg.new_merchant_min_amount:
        return None
    if (row["date"] - first_date).days < cfg.new_merchant_min_history_days:
        return None
    return {
        "kind": "new_merchant",
        "transaction_id": row["id"],
        "category": row["category"],
        "date": row["date"],
        "merchant": row["merchant"],
        "amount": charge,
        "baseline": 0.0,
        "basis": None,
        "message": (
            f"New merchant: {row['merchant']} {fmt_money_whole(charge)} "
            f"on {row['date']:%b} {row['date'].day}"
        ),
    }


def _previous_months(today: date, count: int) -> list[MonthKey]:
    """The `count` completed months before today's month, oldest first."""
    idx = today.year * 12 + today.month - 1
    return [((i // 12), i % 12 + 1) for i in range(idx - count, idx)]


class CategoryBudget(TypedDict):
    """A category's planned spending for the month: fixed lines (rent, a
    subscription) land in full, continuous lines accrue day by day."""

    fixed: float
    variable: float


def _projected(spent: float, by_now: float, whole_month: float) -> float:
    """Where the month lands if it keeps the usual shape: spending so far
    scaled by how much of the month's usual spending is normally in by now;
    with nothing usually in by now, the usual whole month still to come."""
    if by_now > 0:
        return spent * whole_month / by_now
    return spent + whole_month


def running_hot_alerts(
    rows: Iterable[Mapping[str, Any]],
    budget: Mapping[str, CategoryBudget],
    today: date,
    config: AlertConfig | None = None,
    *,
    skip_ids: set[int] | frozenset[int] = frozenset(),
) -> list[Alert]:
    """Categories on pace to overshoot this month.

    ``rows`` is the snapshot's confirmed history (id, date, amount, category),
    covering at least this month and the ``hot_average_months`` before it.
    ``budget`` is each category's plan for today's month. A budgeted category
    is measured against its budget; an unbudgeted one against its average
    over the completed months that have any data (at least
    ``hot_min_average_months`` of them). Rows in ``skip_ids`` (charges with
    their own alert) are left out. Biggest overshoot first."""
    cfg = config or AlertConfig()
    if today.day < cfg.hot_start_day:
        return []
    excluded = set(cfg.excluded_categories)
    current = (today.year, today.month)
    days = monthrange(today.year, today.month)[1]
    window = set(_previous_months(today, cfg.hot_average_months))

    spent_now: dict[str, float] = defaultdict(float)
    # (month, category) -> [spent through today's day, whole month]
    past: dict[tuple[MonthKey, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    past_months: set[MonthKey] = set()
    for row in rows:
        category = row["category"]
        if category in excluded or row["id"] in skip_ids:
            continue
        d = row["date"]
        month = (d.year, d.month)
        spending = -float(row["amount"])  # outflow; refunds net against it
        if month == current:
            if d <= today:
                spent_now[category] += spending
        elif month in window:
            past_months.add(month)
            totals = past[(month, category)]
            totals[1] += spending
            if d.day <= today.day:
                totals[0] += spending

    alerts: list[tuple[float, Alert]] = []
    for category, spent in spent_now.items():
        if spent <= 0:
            continue
        plan = budget.get(category)
        if plan and plan["fixed"] + plan["variable"] > 0:
            basis = "budget"
            whole = plan["fixed"] + plan["variable"]
            by_now = plan["fixed"] + plan["variable"] * today.day / days
        elif len(past_months) >= cfg.hot_min_average_months:
            basis = "average"
            n = len(past_months)
            by_now = sum(past[(m, category)][0] for m in past_months) / n
            whole = sum(past[(m, category)][1] for m in past_months) / n
        else:
            continue
        if whole <= 0:
            continue
        projected = _projected(spent, by_now, whole)
        if (
            projected < whole * cfg.hot_pace_multiple
            or projected - whole < cfg.hot_min_over
        ):
            continue
        usual = "budget" if basis == "budget" else "usually"
        alert: Alert = {
            "kind": "running_hot",
            "transaction_id": None,
            "category": category,
            "date": date(today.year, today.month, 1),
            "merchant": None,
            "amount": spent,
            "baseline": whole,
            "basis": basis,
            "message": (
                f"{category}: {fmt_money_whole(spent)} by the "
                f"{fmt_day_ordinal(today.day)}, {usual} "
                f"{fmt_money_whole(whole)} for the whole month"
            ),
        }
        alerts.append((projected - whole, alert))
    alerts.sort(key=lambda pair: pair[0], reverse=True)
    return [a for _, a in alerts]
