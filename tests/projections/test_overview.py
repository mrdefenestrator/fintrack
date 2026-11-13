"""build_overview: the numbers behind the Overview page and `status`."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.balance_history import record_balance
from fintrack.accounts.repository import add_account
from fintrack.insights.overview import OVERVIEW_MONTHS, build_overview
from fintrack.projections.engine import project, summarize
from tests.projections.test_summary import TODAY, _household


def test_overview_reports_the_12_month_projection(conn, snapshot_id):
    _household(conn, snapshot_id)
    overview = build_overview(conn, snapshot_id, today=TODAY)
    assert overview["months"] == OVERVIEW_MONTHS == 12
    assert overview["cards"] == [
        "net_worth_liquid",
        "cash_flow",
        "debt_cards",
        "needs_attention",
        "spending_alerts",
        "projection_warnings",
    ]
    assert overview["attention"] == []  # no transactions in this household
    assert overview["has_holdings"] is True
    expected = summarize(project(conn, snapshot_id, months=12, today=TODAY))
    assert overview["summary"] == expected


def test_overview_of_an_empty_snapshot(conn, snapshot_id):
    overview = build_overview(conn, snapshot_id, today=TODAY)
    assert overview["has_holdings"] is False
    summary = overview["summary"]
    assert summary["net_worth"]["start"] == 0
    assert summary["warnings"] == []
    assert summary["payoffs"] == []
    assert overview["past"]["liquid"] is None
    assert overview["past"]["net_worth"] is None


def _checking(conn, snapshot_id, name="Checking", balance=5000):
    return add_account(
        conn, snapshot_id, {"name": name, "type": "checking", "balance": balance}
    )


def test_past_change_runs_from_the_month_end_a_year_back(conn, snapshot_id):
    """Now against the end of the same month last year — the first column of
    the Projections window the card opens (actual balances, not projected)."""
    checking = _checking(conn, snapshot_id)  # $5,000 now
    record_balance(
        conn, account_id=checking, balance=Decimal(4000), as_of=date(2025, 7, 20)
    )
    # After that month ended: not part of the baseline.
    record_balance(
        conn, account_id=checking, balance=Decimal(4500), as_of=date(2025, 8, 3)
    )
    past = build_overview(conn, snapshot_id, today=TODAY)["past"]
    assert past["label"] == "Jul 2025"
    assert past["from"] == "2025-07"
    for key in ("liquid", "net_worth"):
        assert past[key] == {
            "then": Decimal(4000),
            "change": Decimal(1000),
            "partial": False,
        }


def test_past_change_counts_only_holdings_with_a_value_then(conn, snapshot_id):
    old = _checking(conn, snapshot_id)
    record_balance(conn, account_id=old, balance=Decimal(4000), as_of=date(2025, 6, 1))
    _checking(conn, snapshot_id, name="Savings", balance=2000)  # opened later
    past = build_overview(conn, snapshot_id, today=TODAY)["past"]
    liquid = past["liquid"]
    assert liquid["partial"] is True
    assert liquid["then"] == Decimal(4000)  # only what was recorded then
    # Like for like: Checking's $1,000, not Savings' $2,000 counted as growth.
    assert liquid["change"] == Decimal(1000)


def test_no_past_change_without_history_that_far_back(conn, snapshot_id):
    _household(conn, snapshot_id)  # every point recorded today
    past = build_overview(conn, snapshot_id, today=TODAY)["past"]
    assert past["liquid"] is None
    assert past["net_worth"] is None


def test_lowest_liquid_month_carries_its_projections_key(conn, snapshot_id):
    _household(conn, snapshot_id)
    summary = build_overview(conn, snapshot_id, today=TODAY)["summary"]
    low = summary["lowest_liquid"]
    year, month = (int(p) for p in low["month_key"].split("-"))
    assert date(year, month, 1).strftime("%b %Y") == low["month_label"]
