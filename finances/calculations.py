"""Business logic and calculations (DESIGN.md: (1)–(6))."""

import calendar
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

_ZERO = Decimal("0")


def _money(x: Any) -> Decimal:
    """Normalize a money value (Decimal from the DB, or int/float/str from
    in-memory data and tests) to Decimal. None becomes 0.

    Goes through str() so float inputs don't carry binary-repr noise
    (Decimal(str(1234.56)) == Decimal('1234.56')), matching the spending
    project's Decimal(str(...)) convention.
    """
    if x is None:
        return _ZERO
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


# Map specific account type to calculation bucket: liquid (in (1) and (2)),
# credit_card (debt in (2)), or other (excluded from (1) and (2)).
_ACCOUNT_TYPE_TO_CALCULATION = {
    "checking": "liquid",
    "savings": "liquid",
    "gift_card": "liquid",
    "wallet": "liquid",
    "digital_wallet": "liquid",
    "credit_card": "credit_card",
    "loan": "other",
    "other": "other",
}

# CLI filter options (parity with GUI)
ACCOUNT_TYPES_CLI = list(_ACCOUNT_TYPE_TO_CALCULATION.keys())
BUDGET_KINDS_CLI = ["income", "expense"]
BUDGET_INCOME_TYPES_CLI = ["salary", "refund", "bonus", "remittance"]
BUDGET_EXPENSE_TYPES_CLI = [
    "housing",
    "insurance",
    "service",
    "utility",
    "product",
    "transport",
    "food",
]
BUDGET_ALL_TYPES_CLI = BUDGET_INCOME_TYPES_CLI + BUDGET_EXPENSE_TYPES_CLI
RECURRENCE_OPTIONS_CLI = [
    "one_time",
    "monthly",
    "biweekly",
    "quarterly",
    "semiannual",
    "annual",
]
ASSETS_KINDS_CLI = ["asset", "debt"]

# Aliases for web and shared use (single source of truth)
ACCOUNT_TYPES = ACCOUNT_TYPES_CLI
BUDGET_KINDS = BUDGET_KINDS_CLI
BUDGET_INCOME_TYPES = BUDGET_INCOME_TYPES_CLI
BUDGET_EXPENSE_TYPES = BUDGET_EXPENSE_TYPES_CLI
BUDGET_ALL_TYPES = BUDGET_ALL_TYPES_CLI
RECURRENCE_OPTIONS = RECURRENCE_OPTIONS_CLI
ASSETS_KINDS = ASSETS_KINDS_CLI


def _credit_card_balance_owed(account: Dict[str, Any]) -> Decimal:
    """Credit card balance owed (amount used). Computed as available - limit."""
    limit = account.get("limit")
    available = account.get("available")
    if limit is not None and available is not None:
        return _money(available) - _money(limit)
    return _ZERO


def liquid_total(accounts: List[Dict[str, Any]]) -> Decimal:
    """(1) Liquid asset/account total (types mapped to liquid: checking, savings, etc.)."""
    return sum(
        (
            _money(a.get("balance"))
            for a in accounts
            if _ACCOUNT_TYPE_TO_CALCULATION.get(a.get("type")) == "liquid"
        ),
        _ZERO,
    )


def credit_card_total(accounts: List[Dict[str, Any]]) -> Decimal:
    """Sum of credit card balances (amount owed). Computed as available - limit per card."""
    return sum(
        (
            _credit_card_balance_owed(a)
            for a in accounts
            if _ACCOUNT_TYPE_TO_CALCULATION.get(a.get("type")) == "credit_card"
        ),
        _ZERO,
    )


def _credit_card_total_with_rewards(accounts: List[Dict[str, Any]]) -> Decimal:
    """Sum of (balance owed + rewards_balance) per credit card. Used for (2) and table total."""
    total = _ZERO
    for a in accounts:
        if _ACCOUNT_TYPE_TO_CALCULATION.get(a.get("type")) != "credit_card":
            continue
        total += _credit_card_balance_owed(a) + _money(a.get("rewards_balance"))
    return total


def liquid_minus_cc(accounts: List[Dict[str, Any]]) -> Decimal:
    """(2) Liquid total minus credit card debts, plus CC rewards. Same as table total."""
    return liquid_total(accounts) + _credit_card_total_with_rewards(accounts)


def _quarter_months(start_month: int) -> set:
    """Months (1–12) in which a quarterly item occurs: start_month and every 3 months.

    Examples:
        _quarter_months(1) -> {1, 4, 7, 10}  # Jan, Apr, Jul, Oct
        _quarter_months(2) -> {2, 5, 8, 11}  # Feb, May, Aug, Nov
        _quarter_months(3) -> {3, 6, 9, 12}  # Mar, Jun, Sep, Dec
    """
    # Convert to 0-based, add offsets, convert back to 1-based
    return {((start_month - 1 + offset) % 12) + 1 for offset in (0, 3, 6, 9)}


def _semiannual_other_month(month: int) -> int:
    """Other month (1–12) for a semiannual item (6 months later)."""
    return (month + 5) % 12 + 1


def _budget_entry_in_month(
    entry: Dict[str, Any], year: int, month: int, day: int | None = None
) -> Decimal:
    """Amount of this income/expense entry that falls in the given month (0 or full amount).
    For continuous monthly entries, day is used to prorate by proportion of month remaining.
    """
    rec = entry.get("recurrence", "")
    amount = _money(entry.get("amount"))
    if rec == "monthly":
        if entry.get("continuous") and day is not None:
            days_in_month = calendar.monthrange(year, month)[1]
            days_remaining = max(0, days_in_month - day)
            return amount * Decimal(days_remaining) / Decimal(days_in_month)
        return amount
    if rec == "one_time":
        d = entry.get("date")
        if not d:
            return _ZERO
        try:
            parsed = date.fromisoformat(d)
            if parsed.year == year and parsed.month == month:
                return amount
        except (TypeError, ValueError):
            pass
        return _ZERO
    if rec == "annual":
        if entry.get("month") == month:
            return amount
        return _ZERO
    if rec == "quarterly":
        m = entry.get("month")
        if m is not None and month in _quarter_months(m):
            return amount
        return _ZERO
    if rec == "semiannual":
        m = entry.get("month")
        if m is not None and (month == m or month == _semiannual_other_month(m)):
            return amount
        return _ZERO
    if rec == "biweekly":
        return amount * 2  # approx 2 pay periods per month
    return _ZERO


def _amount_annual(entry: Dict[str, Any]) -> Decimal:
    """Annualized amount for this entry (for budget --annual). One-time returns amount as-is."""
    rec = entry.get("recurrence", "")
    amount = _money(entry.get("amount"))
    if rec == "monthly":
        return amount * 12
    if rec == "biweekly":
        return amount * 26  # ~26 pay periods per year
    if rec == "annual":
        return amount
    if rec == "quarterly":
        return amount * 4
    if rec == "semiannual":
        return amount * 2
    if rec == "one_time":
        return amount
    return _ZERO


def _subtotal_remainder_of_month(
    entry: Dict[str, Any], year: int, month: int, day: int
) -> Decimal:
    """Expected amount for the remainder of the month for this entry (non-negative).
    Used for the income/expenses table Subtotal column. See DESIGN.md for rules.
    day=0 means start of month (full month remaining).
    """
    rec = entry.get("recurrence", "")
    amount = _money(entry.get("amount"))
    day_effective = max(
        1, day
    )  # for date() and days_remaining; day<dom uses day (0<dom ok)
    if rec == "monthly":
        if entry.get("continuous"):
            days_in_month = calendar.monthrange(year, month)[1]
            days_remaining = (
                max(0, days_in_month - day_effective) if day > 0 else days_in_month
            )
            return amount * Decimal(days_remaining) / Decimal(days_in_month)
        # Not continuous: full amount if we haven't reached dayOfMonth yet
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount  # no day specified, treat as full month
        return amount if day < dom else _ZERO
    if rec == "one_time":
        d = entry.get("date")
        if not d:
            return _ZERO
        try:
            parsed = date.fromisoformat(d)
            if (
                parsed.year == year
                and parsed.month == month
                and parsed >= date(year, month, day_effective)
            ):
                return amount
        except (TypeError, ValueError):
            pass
        return _ZERO
    if rec == "annual":
        if entry.get("month") != month:
            return _ZERO
        dom = entry.get("dayOfMonth")
        doy = entry.get("dayOfYear")
        day_val = dom if dom is not None else doy
        if day_val is None:
            return amount
        return amount if day < day_val else _ZERO
    if rec == "quarterly":
        m = entry.get("month")
        if m is None or month not in _quarter_months(m):
            return _ZERO
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount
        return amount if day < dom else _ZERO
    if rec == "semiannual":
        m = entry.get("month")
        if m is None or (month != m and month != _semiannual_other_month(m)):
            return _ZERO
        dom = entry.get("dayOfMonth")
        if dom is None:
            return amount
        return amount if day < dom else _ZERO
    if rec == "biweekly":
        return amount * 2  # approx 2 pay periods for remainder of month
    return _ZERO


def projected_change_to_eom(
    budget: List[Dict[str, Any]],
    year: int,
    month: int,
    day: int | None = None,
) -> Decimal:
    """(3) Projected change from given day to end of month (income minus expenses remaining).
    Uses remainder-of-month logic: only income/expenses still to occur from day through EOM.
    budget is a unified list of entries with kind: income|expense.
    """
    if day is None:
        today = date.today()
        if (year, month) == (today.year, today.month):
            day = today.day
        else:
            day = 0  # other month: remainder = full month (start of month)
    total = _ZERO
    for e in budget:
        sign = 1 if e.get("kind") == "income" else -1
        total += sign * _subtotal_remainder_of_month(e, year, month, day)
    return total


def _entry_subtotal(entry: Dict[str, Any]) -> Decimal:
    """Subtotal for an asset or debt entry: (value or balance) * quantity (defaults to 1)."""
    field = "value" if entry.get("kind") == "asset" else "balance"
    val = _money(entry.get(field))
    qty = entry.get("quantity")
    return val * (_money(qty) if qty is not None else Decimal(1))


def net_nonliquid_paired(assets: List[Dict[str, Any]]) -> Decimal:
    """(5) Sum of (asset subtotal - debt subtotal) for each debt with assetRef = asset id."""
    asset_by_id = {
        e["id"]: e
        for e in assets
        if e.get("kind") == "asset" and e.get("id") is not None
    }
    total = _ZERO
    for entry in assets:
        if entry.get("kind") != "debt":
            continue
        ref = entry.get("assetRef")
        if ref is None:
            continue
        asset = asset_by_id.get(ref)
        if not asset:
            continue
        total += _entry_subtotal(asset) - _entry_subtotal(entry)
    return total


def net_nonliquid_total(assets: List[Dict[str, Any]]) -> Decimal:
    """(6) Sum of all asset subtotals minus sum of all debt subtotals."""
    total = _ZERO
    for entry in assets:
        if entry.get("kind") == "asset":
            total += _entry_subtotal(entry)
        elif entry.get("kind") == "debt":
            total -= _entry_subtotal(entry)
    return total


def account_funding_needed(
    account: Dict[str, Any],
    accounts: List[Dict[str, Any]],
    budget: List[Dict[str, Any]],
    today: date,
    default_reserve: Any = Decimal("300"),
) -> Dict[str, Any]:
    """Calculate funding needed for a liquid account to cover obligations plus reserve.

    Obligations:
    - CC statement balances for cards where paymentAccountRef == account.id
    - Budget expenses where autoAccountRef == account.id that apply this month

    Reserve: account.minimum_balance if set, else default_reserve.

    Returns a dict with: account, balance, cc_items, cc_total, expense_items,
    expenses_total, reserve, total_obligations, funding_needed, surplus.
    """
    balance = _money(account.get("balance"))
    account_id = account.get("id")

    # CC items: credit cards where paymentAccountRef == this account's id
    cc_items: List[tuple] = []
    for acc in accounts:
        if _ACCOUNT_TYPE_TO_CALCULATION.get(acc.get("type")) != "credit_card":
            continue
        if acc.get("paymentAccountRef") != account_id:
            continue
        stmt_bal = acc.get("statement_balance")
        if stmt_bal is not None:
            amount = _money(stmt_bal)
        else:
            # fallback: limit - available (positive = amount owed)
            amount = -_credit_card_balance_owed(acc)
        cc_items.append((acc, amount))
    cc_total = sum((amt for _, amt in cc_items), _ZERO)

    # Direct expense items: budget expenses where autoAccountRef == this account's id
    # Use _budget_entry_in_month without day so monthly items are not prorated
    expense_items: List[tuple] = []
    for entry in budget:
        if entry.get("kind") != "expense":
            continue
        if entry.get("autoAccountRef") != account_id:
            continue
        amt = _budget_entry_in_month(entry, today.year, today.month)
        if amt > 0:
            expense_items.append((entry, amt))
    expenses_total = sum((amt for _, amt in expense_items), _ZERO)

    # Reserve: use account.minimum_balance if set, else default_reserve
    min_bal = account.get("minimum_balance")
    reserve = _money(min_bal) if min_bal is not None else _money(default_reserve)

    total_obligations = cc_total + expenses_total + reserve
    funding_needed = max(_ZERO, total_obligations - balance)
    surplus = max(_ZERO, balance - total_obligations)

    return {
        "account": account,
        "balance": balance,
        "cc_items": cc_items,
        "cc_total": cc_total,
        "expense_items": expense_items,
        "expenses_total": expenses_total,
        "reserve": reserve,
        "total_obligations": total_obligations,
        "funding_needed": funding_needed,
        "surplus": surplus,
    }
