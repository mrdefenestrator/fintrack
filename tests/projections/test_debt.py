"""Debt and cards (issue #102): per-loan summary, card usage, totals."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.repository import add_account
from fintrack.insights import debt
from fintrack.insights.settings import InsightsConfig
from fintrack.networth.repository import add_asset_entry

TODAY = date(2026, 11, 10)


def _loan(conn, snapshot_id, name, balance, **extra):
    return add_asset_entry(
        conn,
        snapshot_id,
        {"kind": "debt", "name": name, "balance": Decimal(balance), **extra},
        today=TODAY,
    )


def _card(conn, snapshot_id, name, limit, balance):
    return add_account(
        conn,
        snapshot_id,
        {"name": name, "type": "credit_card", "limit": limit, "balance": balance},
        today=TODAY,
    )


def test_loan_summary_with_origination_fields(conn, snapshot_id):
    _loan(
        conn,
        snapshot_id,
        "Car",
        12000,
        type="loan",
        interestRate=0.06,
        originalPrincipal=20000,
        termMonths=60,
        originationDate="2024-01-15",
        statement_due_day_of_month=15,
    )
    (loan,) = debt.debt_and_cards(conn, snapshot_id, TODAY)["loans"]
    assert loan["owed"] == Decimal(12000)
    assert loan["interest"] == Decimal(60)  # 12,000 × 6% ÷ 12
    assert round(loan["payment"], 2) == Decimal("386.66")
    # ~34 payments of $386.66 on $12,000 at 6%, first due Nov 15.
    assert loan["payoff"] == date(2029, 8, 15)


def test_loan_without_origination_fields_has_no_payoff(conn, snapshot_id):
    _loan(conn, snapshot_id, "Family loan", 3000, interestRate=0.03)
    (loan,) = debt.debt_and_cards(conn, snapshot_id, TODAY)["loans"]
    assert loan["interest"] == Decimal("7.5")
    assert loan["payment"] is None and loan["payoff"] is None


def test_totals_and_next_payoff(conn, snapshot_id):
    common = {"type": "loan", "interestRate": 0.05, "originationDate": "2025-01-01"}
    _loan(
        conn,
        snapshot_id,
        "Long",
        9000,
        originalPrincipal=10000,
        termMonths=120,
        **common,
    )
    _loan(
        conn, snapshot_id, "Short", 900, originalPrincipal=1000, termMonths=12, **common
    )
    _loan(conn, snapshot_id, "No terms", 500)
    d = debt.debt_and_cards(conn, snapshot_id, TODAY)
    assert d["loan_owed"] == Decimal(10400)
    assert d["next_payoff"]["name"] == "Short"
    assert d["loan_payment"] == sum(
        loan["payment"] for loan in d["loans"] if loan["payment"]
    )


def test_card_usage_per_card_and_total(conn, snapshot_id):
    _card(conn, snapshot_id, "Low", 10000, -1000)
    _card(conn, snapshot_id, "High", 1000, -400)
    _card(conn, snapshot_id, "No limit", None, -50)  # skipped
    d = debt.debt_and_cards(conn, snapshot_id, TODAY, InsightsConfig())
    by_name = {c["name"]: c for c in d["cards"]}
    assert set(by_name) == {"Low", "High"}
    assert by_name["Low"]["usage"] == Decimal("0.1")
    assert not by_name["Low"]["high"]
    assert by_name["High"]["high"]  # 40% > 30%
    assert d["card_usage"] == Decimal(1400) / Decimal(11000)
    assert d["card_usage_high"] is False


def test_card_in_credit_counts_as_zero_usage(conn, snapshot_id):
    _card(conn, snapshot_id, "Refunded", 1000, 25)
    (card,) = debt.debt_and_cards(conn, snapshot_id, TODAY)["cards"]
    assert card["owed"] == 0 and card["usage"] == 0


def test_nothing_to_report(conn, snapshot_id):
    d = debt.debt_and_cards(conn, snapshot_id, TODAY)
    assert d["loans"] == [] and d["next_payoff"] is None
    assert d["card_usage"] is None
