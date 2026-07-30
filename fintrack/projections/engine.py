"""Projection engine: monthly ending-balance grid per account.

Starting point is each account's current balance (the denormalized latest
balance_history point). For each projected month, in order:

1. **CC autopay** — every credit card with a paymentAccountRef pays its owed
   balance (carried into the month) from that account: an internal transfer,
   so the sum across accounts is unchanged. In the current month the
   transfer is skipped when statement_due_day_of_month has already passed
   (that payment is assumed to be reflected in the current balance).
2. **Budget flows** — each entry's amount for the month (remainder-of-month
   proration in the current month, full-month occurrence afterwards) lands
   on its autoAccountRef; entries without one accumulate in a virtual
   "unassigned" bucket that still affects the totals.
3. **Unscheduled spend** (opt-in) — the estimator's monthly total, prorated
   in the current month, applied to the unassigned bucket.

Totals reuse the key-number calculations: liquid = liquid_total + unassigned;
net worth = liquid_minus_cc + net non-liquid assets + unassigned (assets are
held constant — no return modeling).
"""

import calendar
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import Connection

from fintrack.accounts.repository import get_accounts
from fintrack.budget.recurrence import (
    budget_entry_in_month,
    money,
    month_sequence,
    subtotal_remainder_of_month,
)
from fintrack.budget.repository import get_budget_entries
from fintrack.networth.amortization import scheduled_payment
from fintrack.networth.calculations import (
    asset_contribution,
    liquid_minus_cc,
    liquid_total,
)
from fintrack.networth.repository import get_asset_entries
from fintrack.projections.estimators import (
    unscheduled_monthly_total,
    unscheduled_spend_by_category,
)

_ZERO = Decimal("0")

MIN_MONTHS = 1
MAX_MONTHS = 60
DEFAULT_MONTHS = 12


def clamp_months(months: int) -> int:
    return max(MIN_MONTHS, min(MAX_MONTHS, months))


def _month_label(year: int, month: int) -> str:
    return f"{calendar.month_abbr[month]} {year}"


def _autopay_transfers(
    accounts: List[Dict[str, Any]],
    balances: Dict[int, Decimal],
    *,
    is_current_month: bool,
    day: int,
) -> None:
    """Apply CC autopay for one month, mutating balances in place."""
    for acc in accounts:
        if acc.get("type") != "credit_card":
            continue
        payer_id = acc.get("paymentAccountRef")
        if payer_id is None or payer_id not in balances:
            continue
        due_day = acc.get("statement_due_day_of_month")
        if is_current_month and due_day is not None and day >= due_day:
            continue  # this cycle's payment already happened
        owed = max(_ZERO, -balances[acc["id"]])
        if owed == _ZERO:
            continue
        balances[acc["id"]] += owed
        balances[payer_id] -= owed


def _debt_monthly_payment(debt: Dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Return (monthly_rate, payment) for a debt with amortization data.

    Falls back to (0, 0) when the debt lacks full amortization fields,
    in which case the balance is held constant in the projection.
    """
    principal = debt.get("originalPrincipal")
    rate = debt.get("interestRate")
    term = debt.get("termMonths")
    if principal is None or rate is None or term is None:
        return _ZERO, _ZERO
    payment = scheduled_payment(principal, rate, term)
    if payment is None:
        return _ZERO, _ZERO
    monthly_rate = money(rate) / Decimal(12)
    return monthly_rate, payment


def project(
    conn: Connection,
    snapshot_id: int,
    *,
    months: int = DEFAULT_MONTHS,
    include_estimate: bool = False,
    today: date | None = None,
) -> Dict[str, Any]:
    """Project ending balances for `months` months starting with the current
    (partial) month. Returns the grid plus totals, warnings, and the applied
    estimate (None unless include_estimate)."""
    months = clamp_months(months)
    today = today or date.today()

    accounts = get_accounts(conn, snapshot_id)
    budget = get_budget_entries(conn, snapshot_id)
    assets = get_asset_entries(conn, snapshot_id)

    debts = [e for e in assets if e.get("kind") == "debt"]
    non_debt_assets = [e for e in assets if e.get("kind") != "debt"]
    static_asset_total = sum((asset_contribution(e) for e in non_debt_assets), _ZERO)

    estimate = None
    estimate_monthly = _ZERO
    if include_estimate:
        by_category = unscheduled_spend_by_category(
            conn, snapshot_id, budget, today=today
        )
        estimate_monthly = unscheduled_monthly_total(by_category)
        estimate = {"monthly": estimate_monthly, "by_category": by_category}

    balances: Dict[int, Decimal] = {a["id"]: money(a.get("balance")) for a in accounts}
    unassigned = _ZERO

    debt_balances: Dict[int, Decimal] = {}
    debt_amort: Dict[int, tuple[Decimal, Decimal]] = {}
    debt_due_days: Dict[int, int | None] = {}
    for idx, d in enumerate(debts):
        debt_balances[idx] = money(d.get("balance"))
        debt_amort[idx] = _debt_monthly_payment(d)
        debt_due_days[idx] = d.get("statement_due_day_of_month")

    grid = month_sequence(today.year, today.month, months)
    month_infos = [
        {"year": y, "month": m, "label": _month_label(y, m)} for y, m in grid
    ]
    per_account: Dict[int, List[Decimal]] = {a["id"]: [] for a in accounts}
    per_debt: Dict[int, List[Decimal]] = {idx: [] for idx in range(len(debts))}
    unassigned_series: List[Decimal] = []
    liquid_series: List[Decimal] = []
    net_worth_series: List[Decimal] = []

    for i, (year, month) in enumerate(grid):
        _autopay_transfers(accounts, balances, is_current_month=(i == 0), day=today.day)

        for entry in budget:
            if i == 0:
                amount = subtotal_remainder_of_month(entry, year, month, today.day)
            else:
                amount = budget_entry_in_month(entry, year, month)
            if amount == _ZERO:
                continue
            signed = amount if entry.get("kind") == "income" else -amount
            target = entry.get("autoAccountRef")
            if target in balances:
                balances[target] += signed
            else:
                unassigned += signed

        if estimate_monthly != _ZERO:
            if i == 0:
                days_in_month = calendar.monthrange(year, month)[1]
                remaining = Decimal(max(0, days_in_month - today.day))
                unassigned += estimate_monthly * remaining / Decimal(days_in_month)
            else:
                unassigned += estimate_monthly

        for idx in range(len(debts)):
            monthly_rate, payment = debt_amort[idx]
            if payment != _ZERO:
                due_day = debt_due_days[idx]
                if i == 0 and due_day is not None and today.day >= due_day:
                    pass
                else:
                    bal = debt_balances[idx]
                    debt_balances[idx] = max(
                        _ZERO, bal * (Decimal(1) + monthly_rate) - payment
                    )
            per_debt[idx].append(debt_balances[idx])

        for acc in accounts:
            per_account[acc["id"]].append(balances[acc["id"]])
        unassigned_series.append(unassigned)
        projected = [{**a, "balance": balances[a["id"]]} for a in accounts]
        debt_total = sum(debt_balances.values(), _ZERO)
        liquid_series.append(liquid_total(projected) + unassigned)
        net_worth_series.append(
            liquid_minus_cc(projected) + static_asset_total - debt_total + unassigned
        )

    rows = []
    warnings = []
    for acc in accounts:
        series = per_account[acc["id"]]
        minimum = acc.get("minimum_balance")
        below = [minimum is not None and bal < money(minimum) for bal in series]
        rows.append({"account": acc, "balances": series, "below": below})
        for idx, flag in enumerate(below):
            if flag:
                warnings.append(
                    {
                        "account": acc,
                        "month_index": idx,
                        "month_label": month_infos[idx]["label"],
                        "balance": series[idx],
                        "minimum": money(minimum),
                    }
                )

    for idx, d in enumerate(debts):
        series = [-bal for bal in per_debt[idx]]
        rows.append(
            {
                "account": d,
                "balances": series,
                "below": [False] * len(series),
                "_source": "debt",
            }
        )

    return {
        "start": today,
        "months": month_infos,
        "rows": rows,
        "unassigned": unassigned_series,
        "has_unassigned": any(v != _ZERO for v in unassigned_series),
        "liquid": liquid_series,
        "net_worth": net_worth_series,
        "warnings": warnings,
        "estimate": estimate,
    }
