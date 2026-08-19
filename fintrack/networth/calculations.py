"""Business logic and calculations (DESIGN.md: (1)–(6)).

Recurrence semantics (which months an entry lands in, remainder-of-month
proration) live in fintrack.budget.recurrence; the private names below are
kept as aliases for existing callers.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

from fintrack.budget.recurrence import (
    amount_annual as _amount_annual,  # noqa: F401  (re-exported alias)
    budget_entry_in_month as _budget_entry_in_month,
    money as _money,
    quarter_months as _quarter_months,  # noqa: F401  (re-exported alias)
    semiannual_other_month as _semiannual_other_month,  # noqa: F401
    subtotal_remainder_of_month as _subtotal_remainder_of_month,
)
from fintrack.core.types import (
    ACCOUNT_TYPE_VALUES,
    DEFAULT_TIER,
    HOLDING_TYPE_TIER,
    LIQUIDITY_TIERS,
    LiquidityTier,
)

_ZERO = Decimal("0")


# Map specific account type to calculation bucket: liquid (in (1) and (2)),
# credit_card (debt in (2)), or other (excluded from (1) and (2)).
_ACCOUNT_TYPE_TO_CALCULATION = {
    "checking": "liquid",
    "savings": "liquid",
    "credit_card": "credit_card",
    "gift_card": "liquid",
    "wallet": "liquid",
    "digital_wallet": "liquid",
    "loan": "other",
    "other": "other",
}

# CLI filter options (parity with GUI). Values and order come from the
# canonical account-type list in fintrack.core.types (single source of truth
# shared with the web UI's selects/validation).
ACCOUNT_TYPES_CLI = ACCOUNT_TYPE_VALUES
BUDGET_KINDS_CLI = ["income", "expense"]
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
RECURRENCE_OPTIONS = RECURRENCE_OPTIONS_CLI
ASSETS_KINDS = ASSETS_KINDS_CLI


def _credit_card_balance_owed(account: Dict[str, Any]) -> Decimal:
    """Credit card signed balance (negative = amount owed).

    Prefers the canonical balance (kept in sync by statement imports and
    manual edits); falls back to available - limit for cards that only carry
    the credit-line metadata.
    """
    balance = account.get("balance")
    if balance is not None:
        return _money(balance)
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


def liquid_minus_cc(accounts: List[Dict[str, Any]]) -> Decimal:
    """(2) Liquid total minus credit-card debts.

    Rewards balances are intentionally excluded from calculations for now
    (they stay display-only, QA #2) — so this is simply the liquid total plus
    the summed (signed) credit-card balances.
    """
    return liquid_total(accounts) + credit_card_total(accounts)


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


def _entry_subtotal(
    entry: Dict[str, Any],
    rates: Dict[str, Decimal] | None = None,
) -> Decimal:
    """Subtotal for an asset or debt entry.

    When a cached price exists in *rates* for a non-USD unit, the amount
    is ``quantity * rate`` (ignoring the per-row value/balance).  Otherwise
    falls back to ``(value or balance) * quantity``.
    """
    qty = entry.get("quantity")
    qty_dec = _money(qty) if qty is not None else Decimal(1)

    unit = entry.get("unit") or "USD"
    if unit != "USD" and rates and unit in rates:
        return qty_dec * rates[unit]

    field = "value" if entry.get("kind") == "asset" else "balance"
    val = _money(entry.get(field))
    return val * qty_dec


def net_nonliquid_paired(
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> Decimal:
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
        total += _entry_subtotal(asset, rates) - _entry_subtotal(entry, rates)
    return total


def net_nonliquid_total(
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> Decimal:
    """(6) Sum of all asset subtotals minus sum of all debt subtotals."""
    total = _ZERO
    for entry in assets:
        if entry.get("kind") == "asset":
            total += _entry_subtotal(entry, rates)
        elif entry.get("kind") == "debt":
            total -= _entry_subtotal(entry, rates)
    return total


# ---------------------------------------------------------------------------
# Unified holdings: liquidity tiers + signed net-worth contributions
# ---------------------------------------------------------------------------
#
# Every holding contributes a signed amount to net worth and belongs to exactly
# one liquidity tier (fixed by its type — no per-holding override). The three
# cumulative totals below are signed prefix sums over LIQUIDITY_TIERS:
#     liquid ⊂ investable ⊂ net_worth.


def _tier_for(type_value: Any, unit: Any) -> LiquidityTier:
    """Liquidity tier from a holding's type, with the non-USD symbol cap.

    Tier is fixed by type (unknown/unset -> DEFAULT_TIER). The one exception:
    a holding denominated in a symbol (unit set and not "USD") has sale friction
    and is capped at semi-liquid — so a USD digital wallet stays liquid while a
    BTC digital wallet drops to semi-liquid.
    """
    tier = HOLDING_TYPE_TIER.get(type_value, DEFAULT_TIER)
    if tier == "liquid" and unit and unit != "USD":
        return "semi_liquid"
    return tier


def account_tier(account: Dict[str, Any]) -> LiquidityTier:
    """Liquidity tier of an account (accounts are USD, so no symbol cap)."""
    return _tier_for(account.get("type"), account.get("unit"))


def asset_tier(entry: Dict[str, Any]) -> LiquidityTier:
    """Liquidity tier of an asset/debt entry (symbol-capped by its unit)."""
    return _tier_for(entry.get("type"), entry.get("unit"))


def account_contribution(account: Dict[str, Any]) -> Decimal:
    """Signed net-worth contribution of an account.

    `balance` is already signed (negative = owed on credit cards), so every
    account — credit cards included — contributes its entered balance directly.
    Credit-card rewards and the old available−limit fallback are intentionally
    excluded for now (QA #2): the displayed amount is the entered number, not a
    computed one.
    """
    return _money(account.get("balance"))


def asset_contribution(
    entry: Dict[str, Any],
    rates: Dict[str, Decimal] | None = None,
) -> Decimal:
    """Signed net-worth contribution of an asset/debt entry (assets +, debts -)."""
    subtotal = _entry_subtotal(entry, rates)
    return subtotal if entry.get("kind") == "asset" else -subtotal


def contributions_by_tier(
    accounts: List[Dict[str, Any]],
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> Dict[LiquidityTier, Decimal]:
    """Sum of signed contributions grouped by liquidity tier."""
    totals: Dict[LiquidityTier, Decimal] = {tier: _ZERO for tier in LIQUIDITY_TIERS}
    for account in accounts:
        totals[account_tier(account)] += account_contribution(account)
    for entry in assets:
        totals[asset_tier(entry)] += asset_contribution(entry, rates)
    return totals


def tiered_totals(
    accounts: List[Dict[str, Any]],
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> Dict[str, Decimal]:
    """Cumulative liquidity totals: liquid ⊂ investable ⊂ net_worth.

    - liquid     = spendable now (cash minus credit-card balances).
    - investable = liquid + semi-liquid holdings.
    - net_worth  = investable + illiquid holdings (all assets minus all loans).
    """
    by_tier = contributions_by_tier(accounts, assets, rates)
    liquid = by_tier["liquid"]
    investable = liquid + by_tier["semi_liquid"]
    net_worth = investable + by_tier["illiquid"]
    return {
        "liquid": liquid,
        "investable": investable,
        "net_worth": net_worth,
    }


def net_worth_total(
    accounts: List[Dict[str, Any]],
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> Decimal:
    """Total net worth: signed sum across every account and asset/debt."""
    by_tier = contributions_by_tier(accounts, assets, rates)
    return by_tier["liquid"] + by_tier["semi_liquid"] + by_tier["illiquid"]


def equity_pairs(
    assets: List[Dict[str, Any]],
    rates: Dict[str, Decimal] | None = None,
) -> List[Dict[str, Any]]:
    """Equity for each secured debt linked to an asset via assetRef.

    Returns one dict per linked debt: asset entry, debt entry, the asset and
    debt subtotals, `equity` (asset - debt), and `ltv` (debt / asset, or None
    when the asset subtotal is zero).
    """
    asset_by_id = {
        e["id"]: e
        for e in assets
        if e.get("kind") == "asset" and e.get("id") is not None
    }
    pairs: List[Dict[str, Any]] = []
    for entry in assets:
        if entry.get("kind") != "debt":
            continue
        ref = entry.get("assetRef")
        if ref is None:
            continue
        asset = asset_by_id.get(ref)
        if not asset:
            continue
        asset_subtotal = _entry_subtotal(asset, rates)
        debt_subtotal = _entry_subtotal(entry, rates)
        ltv = (debt_subtotal / asset_subtotal) if asset_subtotal else None
        pairs.append(
            {
                "asset": asset,
                "debt": entry,
                "asset_subtotal": asset_subtotal,
                "debt_subtotal": debt_subtotal,
                "equity": asset_subtotal - debt_subtotal,
                "ltv": ltv,
            }
        )
    return pairs


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
      (cards without a statement balance are skipped — no fallback to the
      card's current balance, QA #7)
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
        if stmt_bal is None:
            # No statement balance → the card contributes nothing to funding.
            # Funding is driven only by linked budget items and CC statement
            # balances, never by the card's current balance (QA #7).
            continue
        cc_items.append((acc, _money(stmt_bal)))
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
