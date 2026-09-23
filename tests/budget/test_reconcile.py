"""Tests for transaction<->budget-entry association (issue #53): linking,
the heuristic suggester, and per-entry budget-vs-actual / missed detection."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import update

from fintrack.budget.reconcile import (
    KindMismatch,
    SnapshotMismatch,
    budget_actuals,
    expected_day,
    link_transaction,
    suggest_links,
    unlink_transaction,
)
from fintrack.budget.repository import delete_budget_entry, get_budget_entries
from fintrack.core.models import imports
from fintrack.ledger.repository.corrections import (
    apply_transaction_correction,
    get_correction,
)
from fintrack.ledger.repository.transactions import (
    get_budget_link_date_ranges,
    get_transactions,
)
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


def _txn(conn, snapshot_id, txn_id):
    return next(
        t for t in get_transactions(conn, snapshot_id=snapshot_id) if t["id"] == txn_id
    )


def test_linked_entry_pins_category(conn, snapshot_id, seeder):
    # The merchant is classified "Housing", but linking to a Subscriptions
    # entry pins the transaction's category to the entry's (issue #53, option A).
    entry = make_expense_entry(
        conn, snapshot_id, amount=15.99, category="Subscriptions"
    )
    txn_id = seeder.add(date(2026, 5, 12), "-15.99", "Streamco", "Housing")
    assert _txn(conn, snapshot_id, txn_id)["category"] == "Housing"

    link_transaction(conn, snapshot_id, txn_id, entry)
    linked = _txn(conn, snapshot_id, txn_id)
    assert linked["category"] == "Subscriptions"
    assert linked["linked_category"] == "Subscriptions"

    unlink_transaction(conn, txn_id)
    assert _txn(conn, snapshot_id, txn_id)["category"] == "Housing"


def test_linked_entry_without_category_keeps_merchant_category(
    conn, snapshot_id, seeder
):
    # An entry with no category doesn't clobber the merchant classification.
    entry = make_expense_entry(conn, snapshot_id, amount=15.99)
    txn_id = seeder.add(date(2026, 5, 12), "-15.99", "Streamco", "Housing")
    link_transaction(conn, snapshot_id, txn_id, entry)
    linked = _txn(conn, snapshot_id, txn_id)
    assert linked["category"] == "Housing"
    assert linked["linked_category"] is None


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


def test_actual_missing_when_due_day_passed(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)  # dayOfMonth 1
    # Linked in April, so it's a tracked recurring charge that didn't show in May.
    april = seeder.add(date(2026, 4, 1), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, april, entry)
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 5, 20)),
        entry,
    )
    assert a.status == "missing"
    assert a.count == 0


def test_actual_upcoming_before_due_day(conn, snapshot_id, seeder):
    entry = make_expense_entry(
        conn, snapshot_id, amount=50, category="Utilities", day_of_month=25
    )
    april = seeder.add(date(2026, 4, 25), "-50.00", "PowerCo", "Utilities")
    link_transaction(conn, snapshot_id, april, entry)
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


# --------------------------------------------------------------------------
# Regressions from the review
# --------------------------------------------------------------------------


def test_link_only_row_is_not_corrected(conn, snapshot_id, seeder):
    # A correction row holding only a link isn't a user correction: the
    # Status=Corrected filter must not match it, Categorized still must.
    entry = make_rent_entry(conn, snapshot_id)
    linked = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    noted = seeder.add(MAY, "-5.00", "Cafe", "Dining")
    link_transaction(conn, snapshot_id, linked, entry)
    apply_transaction_correction(conn, noted, notes="team lunch")

    def ids(status):
        return {
            t["id"]
            for t in get_transactions(conn, snapshot_id=snapshot_id, status=status)
        }

    assert ids("corrected") == {noted}
    assert linked in ids("categorized")


def test_delete_entry_purges_link_only_corrections(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    link_only = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    with_note = seeder.add(date(2026, 6, 1), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, link_only, entry)
    link_transaction(conn, snapshot_id, with_note, entry)
    apply_transaction_correction(conn, with_note, notes="late fee waived")

    idx = next(
        i
        for i, e in enumerate(get_budget_entries(conn, snapshot_id))
        if e["_db_id"] == entry
    )
    delete_budget_entry(conn, snapshot_id, idx)

    # No all-NULL row left behind; the row with a real fix survives, unlinked.
    assert get_correction(conn, link_only) is None
    kept = get_correction(conn, with_note)
    assert kept["budget_entry_ref"] is None
    assert kept["notes"] == "late fee waived"


def test_link_rejects_kind_mismatch(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    paycheck = seeder.add(MAY, "5200.00", "Employer", "Income")
    with pytest.raises(KindMismatch):
        link_transaction(conn, snapshot_id, paycheck, rent)
    assert get_correction(conn, paycheck) is None


def test_tone_flips_with_kind(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    salary = make_entry_of_kind(conn, snapshot_id, "income", amount=5000, day=1)
    over_rent = seeder.add(date(2026, 5, 1), "-2100.00", "Landlord", "Housing")
    over_salary = seeder.add(date(2026, 5, 1), "5200.00", "Employer", "Income")
    link_transaction(conn, snapshot_id, over_rent, rent)
    link_transaction(conn, snapshot_id, over_salary, salary)
    actuals = budget_actuals(conn, snapshot_id, year=2026, month=5)
    assert _by_ref(actuals, rent).status == "over"
    assert _by_ref(actuals, rent).tone == "bad"  # spent more than planned
    assert _by_ref(actuals, salary).status == "over"
    assert _by_ref(actuals, salary).tone == "good"  # earned more than planned


def test_never_linked_entry_is_unlinked_not_missing(conn, snapshot_id):
    entry = make_rent_entry(conn, snapshot_id)
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 5, 20)),
        entry,
    )
    assert a.status == "unlinked"
    assert a.tone == "neutral"


def test_variable_line_uses_category_total(conn, snapshot_id, seeder):
    # A continuous entry that alone claims its category is measured by the
    # category total, linked or not.
    groceries = make_entry_of_kind(
        conn, snapshot_id, "expense", amount=600, category="Groceries", continuous=True
    )
    seeder.add(date(2026, 5, 3), "-120.00", "Market A", "Groceries")
    seeder.add(date(2026, 5, 9), "-80.00", "Market B", "Groceries")
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), groceries)
    assert a.source == "category"
    assert a.actual == Decimal("200.00")
    assert a.count == 2
    assert a.status == "under"


def test_shared_category_falls_back_to_links(conn, snapshot_id, seeder):
    # Two entries claim "Dining", so neither can own the category total.
    dining = make_entry_of_kind(
        conn, snapshot_id, "expense", amount=300, category="Dining", continuous=True
    )
    make_entry_of_kind(conn, snapshot_id, "expense", amount=50, category="Dining")
    seeder.add(date(2026, 5, 3), "-40.00", "Bistro", "Dining")
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), dining)
    assert a.source == "links"
    assert a.status == "unlinked"


def make_entry_of_kind(
    conn, snapshot_id, kind, *, amount, category=None, day=None, continuous=False
):
    from tests.budget.conftest import make_entry

    fields = {"kind": kind, "amount": amount, "recurrence": "monthly"}
    if category:
        fields["category"] = category
    if day is not None:
        fields["dayOfMonth"] = day
    if continuous:
        fields["continuous"] = True
    return make_entry(conn, snapshot_id, **fields)


# --------------------------------------------------------------------------
# Link date ranges + tolerance
# --------------------------------------------------------------------------


def test_link_date_ranges_min_max_per_entry(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    unlinked = make_expense_entry(conn, snapshot_id, amount=10, category="Misc")
    for d in (date(2026, 3, 1), date(2026, 5, 1), date(2026, 4, 1)):
        txn_id = seeder.add(d, "-2000.00", "Landlord", "Housing")
        link_transaction(conn, snapshot_id, txn_id, rent)

    ranges = get_budget_link_date_ranges(conn, snapshot_id)
    assert ranges == {rent: (date(2026, 3, 1), date(2026, 5, 1))}
    assert unlinked not in ranges
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), rent)
    assert a.last_linked == date(2026, 5, 1)


def test_link_date_ranges_scoped_to_snapshot(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, rent)
    other = create_snapshot(conn, "other")
    assert get_budget_link_date_ranges(conn, other) == {}


def test_link_date_ranges_ignore_unconfirmed_imports(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(MAY, "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn_id, rent)
    conn.execute(
        update(imports).where(imports.c.id == seeder.import_id).values(status="staging")
    )
    conn.commit()
    assert get_budget_link_date_ranges(conn, snapshot_id) == {}


@pytest.mark.parametrize(
    ("amount", "status", "drift_flagged"),
    [
        ("-2000.01", "matched", False),  # within a cent: equal
        ("-2000.02", "over", True),  # past a cent: over, and drift
        ("-1999.98", "under", True),
    ],
)
def test_one_tolerance_for_match_and_drift(
    conn, snapshot_id, seeder, amount, status, drift_flagged
):
    rent = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), amount, "Landlord", "Housing")
    (s,) = suggest_links(conn, snapshot_id)
    assert ("price drift" in s.reasons) is drift_flagged

    link_transaction(conn, snapshot_id, txn_id, rent)
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), rent)
    assert a.status == status
