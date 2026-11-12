"""Debt and cards (issue #102): a summary of the snapshot's loans and how much
of its credit card limits are in use.

Per loan: what's owed, the rate, the scheduled payment, the interest part of
this month's payment, and when it's paid off. Per card: owed ÷ limit. Feeds the
Overview "Debt and cards" card, `status`, and the Holdings Loans / Credit Cards
bands. Everything is computed from the holdings; nothing is stored.
"""

from datetime import date
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy.engine import Connection

from fintrack.accounts.repository import get_accounts
from fintrack.insights.settings import InsightsConfig, load_insights_config
from fintrack.networth import calculations
from fintrack.networth.amortization import projected_payoff_date, scheduled_payment
from fintrack.networth.repository import get_asset_entries

_ZERO = Decimal(0)


class LoanSummary(TypedDict):
    id: int
    name: str
    owed: Decimal  # positive dollars owed
    rate: Decimal | None  # annual, e.g. 0.065
    payment: Decimal | None  # scheduled monthly principal + interest
    interest: Decimal | None  # interest part of this month's payment
    payoff: date | None  # when the balance reaches zero at that payment


class CardUsage(TypedDict):
    id: int
    name: str
    owed: Decimal
    limit: Decimal
    usage: Decimal  # owed ÷ limit, 0..1+
    high: bool  # over the configured highlight


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def loan_summary(entry: dict[str, Any], today: date) -> LoanSummary:
    """One loan's numbers. The payment comes from the origination fields
    (amount, rate, term), as Projections uses; without them there's no
    payment or payoff date, but interest still shows when the rate is set."""
    owed = max(_ZERO, -calculations.asset_contribution(entry))
    rate = _dec(entry.get("interestRate"))
    payment = scheduled_payment(
        entry.get("originalPrincipal"),
        entry.get("interestRate"),
        entry.get("termMonths"),
    )
    interest = owed * rate / 12 if rate is not None else None
    payoff = (
        projected_payoff_date(
            owed, rate, payment, today, entry.get("statement_due_day_of_month")
        )
        if payment is not None and rate is not None and owed > 0
        else None
    )
    return LoanSummary(
        id=entry["id"],
        name=entry["name"],
        owed=owed,
        rate=rate,
        payment=payment,
        interest=interest,
        payoff=payoff,
    )


def card_usage(
    account: dict[str, Any], config: InsightsConfig | None = None
) -> CardUsage | None:
    """A card's owed ÷ limit, or None without a positive limit."""
    config = config or load_insights_config()
    limit = _dec(account.get("limit"))
    if limit is None or limit <= 0:
        return None
    owed = max(_ZERO, -calculations._credit_card_balance_owed(account))
    usage = owed / limit
    return CardUsage(
        id=account["id"],
        name=account["name"],
        owed=owed,
        limit=limit,
        usage=usage,
        high=usage * 100 > config.card_usage_high_pct,
    )


def debt_and_cards(
    conn: Connection,
    snapshot_id: int,
    today: date,
    config: InsightsConfig | None = None,
) -> dict[str, Any]:
    """Loans and cards with totals: total owed on loans, this month's payments
    and interest, the next payoff, and total card usage."""
    config = config or load_insights_config()
    loans = [
        loan_summary(e, today)
        for e in get_asset_entries(conn, snapshot_id)
        if e.get("kind") == "debt"
    ]
    cards = [
        usage
        for a in get_accounts(conn, snapshot_id)
        if a.get("type") == "credit_card"
        and (usage := card_usage(a, config)) is not None
    ]
    payoffs = sorted((loan for loan in loans if loan["payoff"]), key=_payoff_key)
    card_owed = sum((c["owed"] for c in cards), _ZERO)
    card_limit = sum((c["limit"] for c in cards), _ZERO)
    card_total = card_owed / card_limit if card_limit else None
    return {
        "loans": loans,
        "loan_owed": sum((loan["owed"] for loan in loans), _ZERO),
        "loan_payment": sum((loan["payment"] or _ZERO for loan in loans), _ZERO),
        "loan_interest": sum((loan["interest"] or _ZERO for loan in loans), _ZERO),
        "next_payoff": payoffs[0] if payoffs else None,
        "cards": cards,
        "card_owed": card_owed,
        "card_limit": card_limit,
        "card_usage": card_total,
        "card_usage_high": (
            card_total is not None and card_total * 100 > config.card_usage_high_pct
        ),
        "card_usage_high_pct": config.card_usage_high_pct,
    }


def _payoff_key(loan: LoanSummary) -> tuple[date, str]:
    return (loan["payoff"], loan["name"])
