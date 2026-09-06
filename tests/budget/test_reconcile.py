"""Tests for transaction<->budget-entry association (issue #53): linking,
the heuristic suggester, and per-entry budget-vs-actual / missed detection."""

from datetime import date
from decimal import Decimal

import pytest

from fintrack.budget.reconcile import (
    SnapshotMismatch,
    budget_actuals,
    expected_day,
    link_transaction,
    suggest_links,
    unlink_transaction,
)
from fintrack.ledger.repository.corrections import get_correction
from fintrack.ledger.repository.transactions import get_transactions
from fintrack.snapshots.repository import create_snapshot

MAY = date(2026, 5, 15)


# --------------------------------------------------------------------------
# Linking
# --------------------------------------------------------------------------


def test_link_and_unlink_round_trip(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")

    link_transaction(conn, snapshot_id, txn_id, entry)
    assert get_correction(conn, txn_id)["budget_entry_ref"] == entry

    unlink_transaction(conn, txn_id)
    # An overlay that existed only for the link is pruned, not left empty.
    assert get_correction(conn, txn_id) is None


def test_unlink_preserves_other_corrections(conn, snapshot_id, seeder):
    from fintrack.ledger.repository.corrections import apply_transaction_correction

    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    apply_transaction_correction(conn, txn_id, notes="check #1023")
    link_transaction(conn, snapshot_id, txn_id, entry)

    unlink_transaction(conn, txn_id)
    row = get_correction(conn, txn_id)
    assert row is not None
    assert row["budget_entry_ref"] is None
    assert row["notes"] == "check #1023"


def test_link_rejects_cross_snapshot_entry(conn, snapshot_id, seeder):
    other = create_snapshot(conn, "other")
    other_entry = make_rent_entry(conn, other)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    with pytest.raises(SnapshotMismatch):
        link_transaction(conn, snapshot_id, txn_id, other_entry)


def test_link_flows_through_transaction_query(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, entry)
    (txn,) = [
        t for t in get_transactions(conn, snapshot_id=snapshot_id) if t["id"] == txn_id
    ]
    assert txn["budget_entry_ref"] == entry


# --------------------------------------------------------------------------
# Suggestions
# --------------------------------------------------------------------------


def test_suggest_exact_match_is_high_confidence(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    suggestions = suggest_links(conn, snapshot_id)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.entry_ref == entry
    assert s.confidence == "high"
    assert "amount exact" in s.reasons


def test_suggest_flags_price_drift(conn, snapshot_id, seeder):
    make_expense_entry(conn, snapshot_id, amount=15.49, category="Subscriptions")
    seeder.add(date(2026, 5, 3), "-17.99", "Streamco", "Subscriptions")
    (s,) = suggest_links(conn, snapshot_id)
    assert "price drift" in s.reasons
    assert s.drift_amount == Decimal("2.50")


def test_suggest_excludes_wrong_sign(conn, snapshot_id, seeder):
    # An expense entry must not match a positive (deposit) transaction.
    make_expense_entry(conn, snapshot_id, amount=100, category="Shopping")
    seeder.add(MAY, "100.00", "Refund", "Shopping")
    assert suggest_links(conn, snapshot_id) == []


def test_suggest_amount_gate_excludes_far_off(conn, snapshot_id, seeder):
    # $5 coffee must not match $2000 rent on category alone.
    make_rent_entry(conn, snapshot_id)
    seeder.add(MAY, "-5.00", "Cafe", "Housing")
    assert suggest_links(conn, snapshot_id) == []


def test_suggest_skips_already_linked(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, entry)
    assert suggest_links(conn, snapshot_id) == []


# --------------------------------------------------------------------------
# Budget-vs-actual + missed detection
# --------------------------------------------------------------------------


def _by_ref(actuals, ref):
    return next(a for a in actuals if a.entry_ref == ref)


def test_actual_matched(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, entry)
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), entry)
    assert a.status == "matched"
    assert a.expected == Decimal("2000.00")
    assert a.actual == Decimal("2000.00")
    assert a.delta == Decimal(0)


def test_actual_over_and_drift(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2100.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, entry)
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), entry)
    assert a.status == "over"
    assert a.delta == Decimal("100.00")
    assert a.drift_amount == Decimal("100.00")


def test_actual_missing_when_due_day_passed(conn, snapshot_id):
    entry = make_rent_entry(conn, snapshot_id)  # dayOfMonth 1
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 5, 20)),
        entry,
    )
    assert a.status == "missing"
    assert a.count == 0


def test_actual_upcoming_before_due_day(conn, snapshot_id):
    entry = make_expense_entry(
        conn, snapshot_id, amount=50, category="Utilities", day_of_month=25
    )
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 5, 10)),
        entry,
    )
    assert a.status == "upcoming"


def test_actual_inactive_when_entry_not_due(conn, snapshot_id):
    # Annual entry due in December is inactive in May.
    entry = make_expense_entry(
        conn, snapshot_id, amount=300, recurrence="annual", month=12
    )
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), entry)
    assert a.status == "inactive"
    assert a.expected == Decimal(0)


def test_expected_day_clamps_to_month_end(conn, snapshot_id):
    entry_dict = {"recurrence": "monthly", "dayOfMonth": 31}
    assert expected_day(entry_dict, 2026, 2) == 28  # Feb clamps


# --------------------------------------------------------------------------
# Entry factories
# --------------------------------------------------------------------------


def make_rent_entry(conn, snapshot_id):
    return make_expense_entry(
        conn, snapshot_id, amount=2000, category="Housing", day_of_month=1
    )


def make_expense_entry(
    conn,
    snapshot_id,
    *,
    amount,
    category=None,
    recurrence="monthly",
    day_of_month=None,
    month=None,
):
    from tests.budget.conftest import make_entry

    fields = {"kind": "expense", "amount": amount, "recurrence": recurrence}
    if category:
        fields["category"] = category
    if day_of_month is not None:
        fields["dayOfMonth"] = day_of_month
    if month is not None:
        fields["month"] = month
    return make_entry(conn, snapshot_id, **fields)
