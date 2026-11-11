"""Year in review (#102): fintrack.insights.year_review."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.balance_history import record_balance
from fintrack.accounts.repository import add_account
from fintrack.insights.year_review import year_review
from tests.projections.conftest import seed_transactions

TODAY = date(2026, 11, 10)


def _checking(conn, snapshot_id, balance=1000, as_of=TODAY):
    return add_account(
        conn,
        snapshot_id,
        {"name": "Checking", "type": "checking", "balance": balance},
        today=as_of,
    )


def _seed(conn, account_id):
    seed_transactions(
        conn,
        account_id,
        [
            # 2025: within the same span (Jan 1 - Nov 10) ...
            (date(2025, 3, 1), "3000", "Acme Payroll", "Income"),
            (date(2025, 3, 2), "-400", "Safeway", "Groceries"),
            (date(2025, 3, 3), "-100", "Shell", "Transport"),
            # ... and after it, so left out of a partial-year comparison.
            (date(2025, 12, 5), "-999", "Safeway", "Groceries"),
            # 2026
            (date(2026, 3, 1), "4000", "Acme Payroll", "Income"),
            (date(2026, 3, 2), "-500", "Safeway", "Groceries"),
            (date(2026, 4, 2), "-100", "Whole Foods", "Groceries"),
            (date(2026, 4, 3), "-50", "Netflix", "Subscriptions"),
            (date(2026, 4, 4), "20", "Safeway", "Groceries"),  # refund
            (date(2026, 5, 1), "-1000", "Savings", "Transfer"),
        ],
    )


def test_partial_year_compares_with_the_same_span(conn, snapshot_id):
    _seed(conn, _checking(conn, snapshot_id))
    r = year_review(conn, snapshot_id, 2026, TODAY)
    assert r["partial"] and r["end"] == TODAY and r["prev_end"] == date(2025, 11, 10)
    by_cat = {c["category"]: c for c in r["categories"]}
    groceries = by_cat["Groceries"]
    assert groceries["total"] == -580 and groceries["prev_total"] == -400
    assert groceries["change_pct"] == 45  # 580 vs 400 in size
    assert by_cat["Subscriptions"]["change_pct"] is None  # nothing last year
    assert (
        by_cat["Transport"]["total"] == 0 and by_cat["Transport"]["prev_total"] == -100
    )
    # Transfer sorts last; the biggest outflow first.
    assert r["categories"][0]["category"] == "Groceries"
    assert r["categories"][-1]["category"] == "Transfer"


def test_cash_flow_and_savings_rate(conn, snapshot_id):
    _seed(conn, _checking(conn, snapshot_id))
    r = year_review(conn, snapshot_id, 2026, TODAY)
    cf = r["cash_flow"]
    assert (cf["income"], cf["spending"], cf["net"]) == (4000, 630, 3370)
    assert round(cf["rate"], 2) == 84.25
    prev = r["prev_cash_flow"]
    assert (prev["income"], prev["spending"]) == (3000, 500)


def test_full_past_year_compares_whole_years(conn, snapshot_id):
    _seed(conn, _checking(conn, snapshot_id))
    r = year_review(conn, snapshot_id, 2025, TODAY)
    assert not r["partial"] and r["end"] == date(2025, 12, 31)
    groceries = next(c for c in r["categories"] if c["category"] == "Groceries")
    assert groceries["total"] == -1399  # the December charge counts


def test_top_merchants_by_spending(conn, snapshot_id):
    _seed(conn, _checking(conn, snapshot_id))
    merchants = year_review(conn, snapshot_id, 2026, TODAY)["top_merchants"]
    # Income and Transfer are left out; Safeway nets its refund.
    assert [(m["merchant"], m["spent"], m["count"]) for m in merchants] == [
        ("Safeway", 480, 2),
        ("Whole Foods", 100, 1),
        ("Netflix", 50, 1),
    ]


def test_net_worth_start_to_end(conn, snapshot_id):
    acct = _checking(conn, snapshot_id, balance=1000, as_of=date(2025, 6, 1))
    record_balance(
        conn, account_id=acct, balance=Decimal(1500), as_of=date(2025, 12, 31)
    )
    record_balance(
        conn, account_id=acct, balance=Decimal(4000), as_of=date(2026, 10, 1)
    )
    nw = year_review(conn, snapshot_id, 2026, TODAY)["net_worth"]
    assert (nw["start"], nw["end"], nw["change"]) == (1500, 4000, 2500)
    assert nw["shows_change"] and nw["covered"] == nw["holdings"] == 1


def test_net_worth_waits_for_enough_history(conn, snapshot_id):
    _checking(conn, snapshot_id, as_of=date(2026, 2, 1))  # no point before 2026
    nw = year_review(conn, snapshot_id, 2026, TODAY)["net_worth"]
    assert nw["covered"] == 0 and not nw["shows_change"]
    assert nw["end"] == 1000 and nw["change"] == 0
