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
net worth = liquid_minus_cc + projected asset total - debt total + unassigned.

Every balance movement is also recorded as a flow (``result["flows"][i]``), so
a month's change on any row is exactly the sum of that row's flows. The
starting point ("now") is returned alongside the grid, and ``summarize`` /
``changes`` derive the page's headline numbers and the month-over-month view.
"""

import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from fintrack.accounts.repository import get_accounts
from fintrack.budget.reconcile import expected_occurrences, is_reversal
from fintrack.budget.recurrence import (
    budget_entry_in_month,
    money,
    month_sequence,
    subtotal_remainder_of_month,
)
from fintrack.budget.repository import get_budget_entries
from fintrack.core.formatting import fmt_account_label
from fintrack.ledger.repository.transactions import get_transactions
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

_ZERO = Decimal(0)

MIN_MONTHS = 1
MAX_MONTHS = 60
DEFAULT_MONTHS = 12


def clamp_months(months: int) -> int:
    return max(MIN_MONTHS, min(MAX_MONTHS, months))


def _month_label(year: int, month: int) -> str:
    return f"{calendar.month_abbr[month]} {year}"


def _flow(holding_id: int | None, kind: str, label: str, amount: Decimal) -> dict:
    """One balance movement. holding_id None means the unassigned bucket."""
    return {"holding_id": holding_id, "kind": kind, "label": label, "amount": amount}


def _autopay_transfers(
    accounts: list[dict[str, Any]],
    balances: dict[int, Decimal],
    *,
    is_current_month: bool,
    day: int,
    flows: list[dict],
) -> None:
    """Apply CC autopay for one month, mutating balances in place and
    recording both sides of each transfer in flows."""
    names = {a["id"]: a.get("name", "") for a in accounts}
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
        flows.append(
            _flow(acc["id"], "autopay", f"Autopay from {names[payer_id]}", owed)
        )
        flows.append(_flow(payer_id, "autopay", f"Autopay to {acc['name']}", -owed))


def _realized_occurrences(
    conn: Connection, snapshot_id: int, year: int, month: int
) -> dict[int, int]:
    """Count transactions linked to each budget entry in the given month,
    keyed by budget-entry db id. Used for current-month projection true-up.
    Reversals (a refund on an expense, a clawback on income) don't realize an
    occurrence, so they aren't counted."""
    counts: dict[int, int] = {}
    for t in get_transactions(conn, year=year, month=month, snapshot_id=snapshot_id):
        ref = t.get("budget_entry_ref")
        if ref is not None and not is_reversal(t.get("linked_kind"), t["amount"]):
            counts[ref] = counts.get(ref, 0) + 1
    return counts


def _month0_budget_amount(
    entry: dict[str, Any],
    year: int,
    month: int,
    day: int,
    realized_count: int,
) -> Decimal:
    """Remaining scheduled amount for the current (partial) month, capped by
    what's still unrealized given linked transactions.

    The day-based remainder (subtotal_remainder_of_month) already drops
    occurrences whose scheduled day has passed; the linked-transaction signal is
    more precise, so we take the smaller of the two for discrete entries. A
    continuous entry represents ongoing daily spend rather than a single charge,
    so it keeps pure day-based proration."""
    day_based = subtotal_remainder_of_month(entry, year, month, day)
    if entry.get("continuous"):
        return day_based
    expected = expected_occurrences(entry, year, month)
    if expected <= 0 or realized_count <= 0:
        return day_based
    unrealized = max(0, expected - realized_count)
    realized_based = money(entry.get("amount")) * Decimal(unrealized)
    return min(day_based, realized_based)


def _asset_monthly_params(asset: dict[str, Any]) -> tuple[Decimal, Decimal]:
    """Return (monthly_rate, monthly_contribution) for a non-debt asset."""
    annual_rate = asset.get("annualReturnRate")
    contribution = asset.get("monthlyContribution")
    monthly_rate = money(annual_rate) / Decimal(12) if annual_rate else _ZERO
    monthly_contrib = money(contribution) if contribution else _ZERO
    return monthly_rate, monthly_contrib


def _debt_monthly_payment(debt: dict[str, Any]) -> tuple[Decimal, Decimal]:
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
) -> dict[str, Any]:
    """Project ending balances for `months` months starting with the current
    (partial) month. Returns the grid plus totals, warnings, and the applied
    estimate (None unless include_estimate)."""
    months = clamp_months(months)
    today = today or datetime.now().astimezone().date()

    accounts = get_accounts(conn, snapshot_id)
    budget = get_budget_entries(conn, snapshot_id)
    assets = get_asset_entries(conn, snapshot_id)

    # Current-month true-up (issue #53): budget flows already realized as linked
    # transactions this month are reflected in the starting balance, so the
    # current month must not project them again. Count linked occurrences per
    # entry for the current month; the budget loop caps month 0's remaining
    # scheduled amount by what is still unrealized.
    realized_this_month = _realized_occurrences(
        conn, snapshot_id, today.year, today.month
    )

    debts = [e for e in assets if e.get("kind") == "debt"]
    non_debt_assets = [e for e in assets if e.get("kind") != "debt"]

    # Fetch current rates for non-USD units so the initial asset valuation
    # uses live prices.  The rate is treated as a fixed constant for future
    # months (no forecasting); annualReturnRate still applies on top.
    rates: dict[str, Decimal] | None = None
    units = {e["unit"] for e in assets if e.get("unit") and e["unit"] != "USD"}
    if units:
        try:
            from fintrack.networth.prices import get_rates

            rates = get_rates(conn, units)
        except Exception:  # noqa: BLE001, S110 — best-effort rates; fall back to per-row value
            pass

    asset_values: dict[int, Decimal] = {}
    asset_params: dict[int, tuple[Decimal, Decimal]] = {}
    for idx, a in enumerate(non_debt_assets):
        asset_values[idx] = asset_contribution(a, rates=rates)
        asset_params[idx] = _asset_monthly_params(a)

    estimate = None
    estimate_monthly = _ZERO
    if include_estimate:
        by_category = unscheduled_spend_by_category(
            conn, snapshot_id, budget, today=today
        )
        estimate_monthly = unscheduled_monthly_total(by_category)
        estimate = {"monthly": estimate_monthly, "by_category": by_category}

    balances: dict[int, Decimal] = {a["id"]: money(a.get("balance")) for a in accounts}
    unassigned = _ZERO
    start_balances = dict(balances)
    start_asset_values = dict(asset_values)

    debt_balances: dict[int, Decimal] = {}
    debt_amort: dict[int, tuple[Decimal, Decimal]] = {}
    debt_due_days: dict[int, int | None] = {}
    for idx, d in enumerate(debts):
        debt_balances[idx] = money(d.get("balance"))
        debt_amort[idx] = _debt_monthly_payment(d)
        debt_due_days[idx] = d.get("statement_due_day_of_month")
    start_debt_balances = dict(debt_balances)

    grid = month_sequence(today.year, today.month, months)
    month_infos = [
        {"year": y, "month": m, "label": _month_label(y, m)} for y, m in grid
    ]
    per_account: dict[int, list[Decimal]] = {a["id"]: [] for a in accounts}
    per_debt: dict[int, list[Decimal]] = {idx: [] for idx in range(len(debts))}
    per_asset: dict[int, list[Decimal]] = {
        idx: [] for idx in range(len(non_debt_assets))
    }
    unassigned_series: list[Decimal] = []
    liquid_series: list[Decimal] = []
    net_worth_series: list[Decimal] = []
    flows_by_month: list[list[dict]] = []

    for i, (year, month) in enumerate(grid):
        flows: list[dict] = []
        flows_by_month.append(flows)
        _autopay_transfers(
            accounts, balances, is_current_month=(i == 0), day=today.day, flows=flows
        )

        for entry in budget:
            if i == 0:
                amount = _month0_budget_amount(
                    entry,
                    year,
                    month,
                    today.day,
                    realized_this_month.get(entry.get("_db_id"), 0),
                )
            else:
                amount = budget_entry_in_month(entry, year, month)
            if amount == _ZERO:
                continue
            signed = amount if entry.get("kind") == "income" else -amount
            target = entry.get("autoAccountRef")
            if target in balances:
                balances[target] += signed
            else:
                target = None
                unassigned += signed
            flows.append(
                _flow(
                    target,
                    "income" if entry.get("kind") == "income" else "expense",
                    entry.get("description") or "(budget entry)",
                    signed,
                )
            )

        if estimate_monthly != _ZERO:
            if i == 0:
                days_in_month = calendar.monthrange(year, month)[1]
                remaining = Decimal(max(0, days_in_month - today.day))
                spend = estimate_monthly * remaining / Decimal(days_in_month)
            else:
                spend = estimate_monthly
            if spend != _ZERO:
                unassigned += spend
                flows.append(
                    _flow(None, "estimate", "Unscheduled spend (estimate)", spend)
                )

        for idx in range(len(debts)):
            monthly_rate, payment = debt_amort[idx]
            if payment != _ZERO:
                due_day = debt_due_days[idx]
                if i == 0 and due_day is not None and today.day >= due_day:
                    pass
                else:
                    bal = debt_balances[idx]
                    with_interest = bal * (Decimal(1) + monthly_rate)
                    debt_balances[idx] = max(_ZERO, with_interest - payment)
                    # Debt rows display as negative (owed), so interest is a
                    # negative flow and the payment a positive one.
                    debt_id = debts[idx].get("id")
                    interest = with_interest - bal
                    if interest != _ZERO:
                        flows.append(_flow(debt_id, "interest", "Interest", -interest))
                    paid = with_interest - debt_balances[idx]
                    if paid != _ZERO:
                        flows.append(
                            _flow(debt_id, "payment", "Scheduled payment", paid)
                        )
            per_debt[idx].append(debt_balances[idx])

        for idx in range(len(non_debt_assets)):
            monthly_rate, monthly_contrib = asset_params[idx]
            if monthly_rate != _ZERO or monthly_contrib != _ZERO:
                old = asset_values[idx]
                asset_values[idx] = old * (Decimal(1) + monthly_rate) + monthly_contrib
                asset_id = non_debt_assets[idx].get("id")
                growth = asset_values[idx] - old - monthly_contrib
                if growth != _ZERO:
                    flows.append(
                        _flow(
                            asset_id,
                            "growth",
                            "Growth" if growth > _ZERO else "Depreciation",
                            growth,
                        )
                    )
                if monthly_contrib != _ZERO:
                    flows.append(
                        _flow(asset_id, "contribution", "Contribution", monthly_contrib)
                    )
            per_asset[idx].append(asset_values[idx])

        for acc in accounts:
            per_account[acc["id"]].append(balances[acc["id"]])
        unassigned_series.append(unassigned)
        projected = [{**a, "balance": balances[a["id"]]} for a in accounts]
        debt_total = sum(debt_balances.values(), _ZERO)
        asset_total = sum(asset_values.values(), _ZERO)
        liquid_series.append(liquid_total(projected) + unassigned)
        net_worth_series.append(
            liquid_minus_cc(projected) + asset_total - debt_total + unassigned
        )

    rows = []
    warnings = []
    for acc in accounts:
        series = per_account[acc["id"]]
        minimum = acc.get("minimum_balance")
        below = [minimum is not None and bal < money(minimum) for bal in series]
        rows.append(
            {
                "account": acc,
                "start": start_balances[acc["id"]],
                "balances": series,
                "below": below,
            }
        )
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
                "start": -start_debt_balances[idx],
                "balances": series,
                "below": [False] * len(series),
                "_source": "debt",
            }
        )

    for idx, a in enumerate(non_debt_assets):
        rows.append(
            {
                "account": a,
                "start": start_asset_values[idx],
                "balances": per_asset[idx],
                "below": [False] * len(per_asset[idx]),
                "_source": "asset",
            }
        )

    start_debt_total = sum(start_debt_balances.values(), _ZERO)
    start_asset_total = sum(start_asset_values.values(), _ZERO)
    return {
        "start": today,
        "start_liquid": liquid_total(accounts),
        "start_net_worth": liquid_minus_cc(accounts)
        + start_asset_total
        - start_debt_total,
        "months": month_infos,
        "rows": rows,
        "unassigned": unassigned_series,
        "has_unassigned": any(v != _ZERO for v in unassigned_series),
        "liquid": liquid_series,
        "net_worth": net_worth_series,
        "warnings": warnings,
        "estimate": estimate,
        "flows": flows_by_month,
    }


def changes(start: Decimal, series: list[Decimal]) -> list[Decimal]:
    """Month-over-month change: month 0 against the starting value, then each
    month against the one before."""
    previous = [start, *series[:-1]]
    return [cur - prev for cur, prev in zip(series, previous, strict=True)]


def _trend(start: Decimal, series: list[Decimal]) -> dict[str, Decimal]:
    end = series[-1]
    change = end - start
    return {
        "start": start,
        "end": end,
        "change": change,
        "per_month": change / Decimal(len(series)),
    }


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    """Headline numbers for a projection: liquid and net-worth trend, the
    lowest liquid month, loan payoffs within the horizon, and minimum-balance
    warnings collapsed to one line per account."""
    months = result["months"]
    liquid = result["liquid"]
    low_idx = min(range(len(liquid)), key=lambda i: liquid[i])

    payoffs = []
    for row in result["rows"]:
        if row.get("_source") != "debt" or row["start"] >= _ZERO:
            continue
        paid_idx = next((i for i, b in enumerate(row["balances"]) if b >= _ZERO), None)
        if paid_idx is not None:
            payoffs.append(
                {
                    "name": row["account"]["name"],
                    "month_label": months[paid_idx]["label"],
                }
            )

    by_account: dict[int, dict[str, Any]] = {}
    for w in result["warnings"]:
        acc_id = w["account"]["id"]
        if acc_id not in by_account:
            by_account[acc_id] = {
                "account": w["account"],
                "first_month_label": w["month_label"],
                "lowest": w["balance"],
                "minimum": w["minimum"],
                "months_below": 0,
            }
        entry = by_account[acc_id]
        entry["months_below"] += 1
        entry["lowest"] = min(entry["lowest"], w["balance"])

    return {
        "liquid": _trend(result["start_liquid"], liquid),
        "net_worth": _trend(result["start_net_worth"], result["net_worth"]),
        "end_label": months[-1]["label"],
        "lowest_liquid": {
            "amount": liquid[low_idx],
            "month_label": months[low_idx]["label"],
        },
        "payoffs": payoffs,
        "warnings": list(by_account.values()),
    }


def month_breakdown(result: dict[str, Any], month_index: int) -> list[dict[str, Any]]:
    """One month's flows grouped by holding (rows order, unassigned last).
    Each group: {"name", "flows", "total"}; holdings with no flows are left out."""
    flows = result["flows"][month_index]
    groups = []
    keys: list[tuple[int | None, str]] = [
        (row["account"]["id"], fmt_account_label(row["account"]))
        for row in result["rows"]
    ] + [(None, "(unassigned)")]
    for holding_id, name in keys:
        mine = [f for f in flows if f["holding_id"] == holding_id]
        if mine:
            groups.append(
                {
                    "name": name,
                    "flows": mine,
                    "total": sum((f["amount"] for f in mine), _ZERO),
                }
            )
    return groups
