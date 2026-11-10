"""Spending alerts over a real snapshot (issue #101): running hot against the
budget, hidden duplicates, dismissals, snapshot scoping, Overview, and CLI."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.cli import cli
from fintrack.core.models import transactions
from fintrack.insights.alerts import (
    alerts_by_transaction,
    get_alerts,
    get_import_alerts,
)
from fintrack.insights.overview import build_overview
from fintrack.ledger.repository.alerts import (
    dismiss_category_alert,
    dismiss_transaction_alert,
    undismiss_category_alert,
)
from fintrack.ledger.repository.duplicates import (
    add_duplicate_flags,
    resolve_duplicate,
)
from fintrack.snapshots.repository import create_snapshot
from tests.projections.conftest import seed_transactions

TODAY = date(2026, 11, 12)


def _seed(conn, snapshot_id, extra=()):
    """Dining at $600/mo spread over each month for six months, then $420 by
    the 12th; a $300 charge at a hardware store that usually charges $50
    (that one charge is its own alert, so it doesn't also make Shopping hot)."""
    card = add_account(conn, snapshot_id, {"name": "Card", "type": "credit_card"})
    rows = [
        (date(2026, m, d), Decimal(-200), "BISTRO", "Dining")
        for m in range(5, 11)
        for d in (5, 15, 25)
    ]
    rows += [
        (date(2026, m, 3), Decimal(-50), "HARDWARE", "Shopping") for m in (8, 9, 10)
    ]
    rows += [
        (date(2026, 11, 3), Decimal(-210), "BISTRO", "Dining"),
        (date(2026, 11, 10), Decimal(-210), "BISTRO", "Dining"),
        (date(2026, 11, 9), Decimal(-300), "HARDWARE", "Shopping"),
        (date(2026, 11, 9), Decimal(5000), "PAYROLL", "Income"),
        *extra,
    ]
    seed_transactions(conn, card, rows)
    return card


def _big_charge_id(conn):
    return conn.execute(
        select(transactions.c.id).where(transactions.c.amount == -300)
    ).scalar()


def test_alerts_for_a_snapshot(conn, snapshot_id):
    _seed(conn, snapshot_id)
    found = get_alerts(conn, snapshot_id, TODAY)
    assert [a["kind"] for a in found] == ["running_hot", "large_charge"]
    hot, large = found
    assert (
        hot["message"] == "Dining: $420 by the 12th, usually $600 for the whole month"
    )
    assert large["transaction_id"] == _big_charge_id(conn)


def test_running_hot_measures_against_the_budget(conn, snapshot_id):
    _seed(conn, snapshot_id)
    add_budget_entry(
        conn,
        snapshot_id,
        {
            "description": "Eating out",
            "kind": "expense",
            "amount": 1000,
            "recurrence": "monthly",
            "continuous": True,
            "category": "Dining",
        },
    )
    # On pace for $1,050 vs a $1,000 budget: under 1.3x, so quiet.
    assert [a["kind"] for a in get_alerts(conn, snapshot_id, TODAY)] == ["large_charge"]


def test_hidden_duplicates_are_left_out(conn, snapshot_id):
    _seed(
        conn, snapshot_id, [(date(2026, 11, 9), Decimal(-300), "HARDWARE", "Shopping")]
    )
    first, second = conn.execute(
        select(transactions.c.id)
        .where(transactions.c.amount == -300)
        .order_by(transactions.c.id)
    ).scalars()
    add_duplicate_flags(conn, [(second, first, "same amount")], source="scan")
    resolve_duplicate(conn, second, "duplicate")
    flagged = [a["transaction_id"] for a in get_alerts(conn, snapshot_id, TODAY)]
    assert second not in flagged and first in flagged


def test_dismissals_hide_alerts_and_are_scoped(conn, snapshot_id):
    _seed(conn, snapshot_id)
    other = create_snapshot(conn, "other")
    txn = _big_charge_id(conn)
    dismiss_transaction_alert(conn, snapshot_id, txn)
    dismiss_transaction_alert(conn, snapshot_id, txn)  # idempotent
    dismiss_category_alert(conn, snapshot_id, "Dining", TODAY)
    assert get_alerts(conn, snapshot_id, TODAY) == []
    assert len(get_alerts(conn, snapshot_id, TODAY, include_dismissed=True)) == 2
    # Dismissing is per month: next month's Dining alert would show again.
    assert undismiss_category_alert(conn, snapshot_id, "Dining", date(2026, 11, 30))
    assert [a["kind"] for a in get_alerts(conn, snapshot_id, TODAY)] == ["running_hot"]
    # Another snapshot can't dismiss this one's transactions.
    with pytest.raises(LookupError):
        dismiss_transaction_alert(conn, other, txn)


def test_import_alerts_cover_just_that_import(conn, snapshot_id):
    card = _seed(conn, snapshot_id)
    seed_transactions(
        conn, card, [(date(2026, 11, 10), Decimal(-90), "STRANGER", "Shopping")]
    )
    new_import = conn.execute(
        select(transactions.c.import_id).where(transactions.c.amount == -90)
    ).scalar()
    (alert,) = get_import_alerts(conn, snapshot_id, new_import, TODAY)
    assert alert["kind"] == "new_merchant"


def test_overview_spending_alerts_card(conn, snapshot_id):
    _seed(conn, snapshot_id)
    card = build_overview(conn, snapshot_id, today=TODAY)["spending_alerts"]
    assert card["count"] == 2
    assert [i["kind"] for i in card["items"]] == ["running_hot", "large_charge"]
    assert card["more"] == 0
    assert card["window_days"] == 30


def _invoke(db_path, *args):
    return CliRunner().invoke(
        cli, ["--db", str(db_path), *args], catch_exceptions=False
    )


def test_cli_alerts_and_status(tmp_path):
    db = tmp_path / "t.db"
    assert _invoke(db, "snapshots", "add", "main").exit_code == 0
    assert "Nothing unusual." in _invoke(db, "alerts").output
    status = _invoke(db, "status").output
    assert "Spending alerts: All clear" in status

    from fintrack.core.db import get_engine
    from fintrack.snapshots.repository import get_snapshot_id

    engine = get_engine(str(db))
    today = datetime.now().astimezone().date()
    with engine.connect() as conn:
        sid = get_snapshot_id(conn, "main")
        card = add_account(conn, sid, {"name": "Card", "type": "credit_card"})
        seed_transactions(
            conn,
            card,
            [
                (today - timedelta(days=100), Decimal(-10), "OLD", "Utilities"),
                (today, Decimal(-75), "STRANGER", "Utilities"),
            ],
        )
        txn = conn.execute(
            select(transactions.c.id).where(transactions.c.amount == -75)
        ).scalar()
    engine.dispose()

    out = _invoke(db, "alerts").output
    assert "New merchant: STRANGER $75" in out
    assert "Spending alerts: 1 thing to look at" in _invoke(db, "status").output

    assert _invoke(db, "alerts", "dismiss", str(txn)).exit_code == 0
    assert "Nothing unusual." in _invoke(db, "alerts").output
    assert "dismissed" in _invoke(db, "alerts", "--all").output
    assert _invoke(db, "alerts", "undo", str(txn)).exit_code == 0
    assert "STRANGER" in _invoke(db, "alerts", "list").output

    assert _invoke(db, "alerts", "dismiss", "--category", "Dining").exit_code == 0
    assert _invoke(db, "alerts", "undo", "--category", "Dining").exit_code == 0
    bad = CliRunner().invoke(cli, ["--db", str(db), "alerts", "dismiss"])
    assert bad.exit_code != 0


def test_flagged_rows_include_a_hot_categorys_month(conn, snapshot_id):
    """Flagged lists the rows behind every alert: the big charge itself, and
    this month's rows in a category running hot."""
    _seed(conn, snapshot_id)
    flagged = alerts_by_transaction(conn, snapshot_id, TODAY)
    rows = {
        r.id: r
        for r in conn.execute(
            select(transactions.c.id, transactions.c.date, transactions.c.amount)
        )
    }
    by_kind = {}
    for txn_id, alert in flagged.items():
        by_kind.setdefault(alert["kind"], []).append(rows[txn_id])
    assert [r.amount for r in by_kind["large_charge"]] == [-300]
    hot = by_kind["running_hot"]
    assert len(hot) == 2  # the two November Dining charges, not the history
    assert all(r.date.month == 11 and r.amount == -210 for r in hot)

    # Dismissing the category for the month unflags its rows.
    dismiss_category_alert(conn, snapshot_id, "Dining", TODAY)
    kinds = {
        a["kind"] for a in alerts_by_transaction(conn, snapshot_id, TODAY).values()
    }
    assert kinds == {"large_charge"}
