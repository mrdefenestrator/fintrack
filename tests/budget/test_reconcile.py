"""Tests for transaction<->budget-entry association (issue #53): linking,
the heuristic suggester, and per-entry budget-vs-actual / missed detection."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from fintrack.budget.reconcile import (
    SnapshotMismatch,
    budget_actuals,
    dismiss_suggestion,
    expected_day,
    is_reversal,
    link_transaction,
    suggest_links,
    undismiss_suggestions,
    unlink_transaction,
)
from fintrack.budget.repository import delete_budget_entry, get_budget_entries
from fintrack.core.models import (
    imports,
    link_suggestion_dismissals,
    transaction_duplicates,
)
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


def test_dismissed_suggestion_is_not_proposed_again(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    dismiss_suggestion(conn, snapshot_id, txn_id, entry)
    assert suggest_links(conn, snapshot_id) == []
    # Dismissal only filters suggestions — a manual link still works.
    link_transaction(conn, snapshot_id, txn_id, entry)
    assert get_correction(conn, txn_id)["budget_entry_ref"] == entry


def test_dismissal_falls_back_to_next_best_entry(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    other = make_expense_entry(conn, snapshot_id, amount=1900)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    assert suggest_links(conn, snapshot_id)[0].entry_ref == rent
    dismiss_suggestion(conn, snapshot_id, txn_id, rent)
    (s,) = suggest_links(conn, snapshot_id)
    assert s.entry_ref == other


def test_dismiss_is_idempotent_and_undismiss_restores(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    dismiss_suggestion(conn, snapshot_id, txn_id, entry)
    dismiss_suggestion(conn, snapshot_id, txn_id, entry)
    assert undismiss_suggestions(conn, txn_id) == 1
    assert [s.entry_ref for s in suggest_links(conn, snapshot_id)] == [entry]


def test_dismiss_rejects_cross_snapshot_entry(conn, snapshot_id, seeder):
    other_snap = create_snapshot(conn, "other")
    foreign = make_rent_entry(conn, other_snap)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    with pytest.raises(SnapshotMismatch):
        dismiss_suggestion(conn, snapshot_id, txn_id, foreign)


def test_dismissal_cascades_with_budget_entry(conn, snapshot_id, seeder):
    entry = make_rent_entry(conn, snapshot_id)
    txn_id = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    dismiss_suggestion(conn, snapshot_id, txn_id, entry)
    delete_budget_entry(conn, snapshot_id, 0)
    count = conn.execute(
        select(func.count()).select_from(link_suggestion_dismissals)
    ).scalar()
    assert count == 0


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


# --------------------------------------------------------------------------
# Reversals: refunds on expenses, clawbacks on income
# --------------------------------------------------------------------------


def test_is_reversal():
    assert is_reversal("expense", "20.00")
    assert not is_reversal("expense", "-20.00")
    assert is_reversal("income", "-20.00")
    assert not is_reversal("income", "20.00")


def test_refund_links_to_expense_entry(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    refund = seeder.add(MAY, "150.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, refund, rent)
    assert get_correction(conn, refund)["budget_entry_ref"] == rent


def test_refund_nets_against_expense_actual(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    charge = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    refund = seeder.add(date(2026, 5, 20), "200.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, charge, rent)
    link_transaction(conn, snapshot_id, refund, rent)
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), rent)
    assert a.actual == Decimal("1800.00")
    assert a.reversed_amount == Decimal("200.00")
    assert a.count == 1  # the refund isn't an occurrence
    assert a.status == "under"
    assert a.drift_amount == Decimal(0)  # drift uses the charge alone


def test_refund_alone_does_not_count_as_paid(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    april = seeder.add(date(2026, 4, 1), "-2000.00", "Landlord", "Housing")
    refund = seeder.add(date(2026, 5, 10), "50.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, april, rent)
    link_transaction(conn, snapshot_id, refund, rent)
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 6, 1)),
        rent,
    )
    assert a.status == "missing"
    assert a.count == 0
    assert a.actual == Decimal("-50.00")


def test_refund_alone_does_not_make_entry_tracked(conn, snapshot_id, seeder):
    # A refund linked last month is not a payment: with nothing linked this
    # month the entry is "unlinked", not "missing", and has no last-seen date.
    gym = make_expense_entry(conn, snapshot_id, amount=45, day_of_month=1)
    refund = seeder.add(date(2026, 4, 10), "10.00", "Gym")
    link_transaction(conn, snapshot_id, refund, gym)
    assert get_budget_link_date_ranges(conn, snapshot_id) == {}
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 6, 1)),
        gym,
    )
    assert a.status == "unlinked"
    assert a.last_linked is None


def test_last_seen_ignores_later_refund(conn, snapshot_id, seeder):
    rent = make_rent_entry(conn, snapshot_id)
    charge = seeder.add(date(2026, 4, 1), "-2000.00", "Landlord", "Housing")
    refund = seeder.add(date(2026, 4, 20), "100.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, charge, rent)
    link_transaction(conn, snapshot_id, refund, rent)
    assert get_budget_link_date_ranges(conn, snapshot_id)[rent] == (
        date(2026, 4, 1),
        date(2026, 4, 1),
    )


def test_clawback_nets_against_income_actual(conn, snapshot_id, seeder):
    salary = make_entry_of_kind(conn, snapshot_id, "income", amount=5000, day=1)
    pay = seeder.add(date(2026, 5, 1), "5000.00", "Employer", "Income")
    clawback = seeder.add(date(2026, 5, 15), "-300.00", "Employer", "Income")
    link_transaction(conn, snapshot_id, pay, salary)
    link_transaction(conn, snapshot_id, clawback, salary)
    a = _by_ref(budget_actuals(conn, snapshot_id, year=2026, month=5), salary)
    assert a.actual == Decimal("4700.00")
    assert a.reversed_amount == Decimal("300.00")
    assert a.count == 1
    assert a.status == "under"
    assert a.tone == "bad"


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


def _groceries_on(conn, snapshot_id, seeder, spent, today):
    """A $600/mo variable Groceries line with `spent` so far in Nov 2026."""
    groceries = make_entry_of_kind(
        conn, snapshot_id, "expense", amount=600, category="Groceries", continuous=True
    )
    seeder.add(date(2026, 11, 2), f"-{spent}", "Market", "Groceries")
    return _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=11, today=today),
        groceries,
    )


def test_variable_line_mid_month_is_judged_against_pace(conn, snapshot_id, seeder):
    # Nov 6 is 6/30 = 20% of the month: pace is $120, so $118 is on pace, not
    # "under" just because the month isn't over (#103).
    a = _groceries_on(conn, snapshot_id, seeder, "118.00", date(2026, 11, 6))
    assert a.pace == Decimal("120.00")
    assert a.status == "on_pace"
    assert a.tone == "neutral"


@pytest.mark.parametrize(
    "spent,status",
    [
        ("178.00", "over"),  # 48% over pace, though under the month's $600
        ("100.00", "under"),  # 17% behind pace
        ("131.00", "on_pace"),  # within 10% of $120
        ("650.00", "over"),  # past the whole month
    ],
)
def test_variable_line_pace_status(conn, snapshot_id, seeder, spent, status):
    a = _groceries_on(conn, snapshot_id, seeder, spent, date(2026, 11, 6))
    assert a.status == status


def test_variable_line_past_month_ignores_pace(conn, snapshot_id, seeder):
    # Once the month is over, the full expected amount is the yardstick.
    a = _groceries_on(conn, snapshot_id, seeder, "178.00", date(2026, 12, 3))
    assert a.pace is None
    assert a.status == "under"


def test_linked_bill_has_no_pace(conn, snapshot_id, seeder):
    # Pace only applies to category-measured variable lines.
    rent = make_rent_entry(conn, snapshot_id)
    txn = seeder.add(date(2026, 11, 1), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, txn, rent)
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=11, today=date(2026, 11, 6)),
        rent,
    )
    assert a.pace is None
    assert a.status == "matched"


def test_signed_figures_follow_kind(conn, snapshot_id, seeder):
    # Expenses negative, income positive; a + signed delta beats the budget.
    groceries = make_entry_of_kind(
        conn, snapshot_id, "expense", amount=600, category="Groceries", continuous=True
    )
    pay = make_entry_of_kind(
        conn, snapshot_id, "income", amount=5000, category="Salary", continuous=True
    )
    seeder.add(date(2026, 5, 3), "-450.00", "Market", "Groceries")
    seeder.add(date(2026, 5, 15), "5200.00", "Employer", "Salary")
    rows = budget_actuals(conn, snapshot_id, year=2026, month=5)
    g, p = _by_ref(rows, groceries), _by_ref(rows, pay)
    assert (g.signed_expected, g.signed_actual) == (Decimal(-600), Decimal("-450.00"))
    assert g.signed_delta == Decimal("150.00")  # $150 under: good
    assert (p.signed_expected, p.signed_actual) == (Decimal(5000), Decimal("5200.00"))
    assert p.signed_delta == Decimal("200.00")  # $200 ahead: good


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


def test_suggest_skips_category_measured_lines(conn, snapshot_id, seeder):
    # A by-category line already counts the transaction; a link would only
    # re-pin the category, so it's never suggested.
    make_entry_of_kind(
        conn, snapshot_id, "expense", amount=180, category="Transport", continuous=True
    )
    seeder.add(date(2026, 5, 7), "-189.99", "Amazon", "Shopping")
    assert suggest_links(conn, snapshot_id) == []


def test_suggest_keeps_continuous_line_sharing_its_category(conn, snapshot_id, seeder):
    # Two entries claim "Dining", so the continuous one is measured by links
    # and stays suggestable.
    dining = make_entry_of_kind(
        conn, snapshot_id, "expense", amount=300, category="Dining", continuous=True
    )
    make_entry_of_kind(conn, snapshot_id, "expense", amount=50, category="Dining")
    seeder.add(date(2026, 5, 3), "-300.00", "Bistro", "Dining")
    (s,) = suggest_links(conn, snapshot_id)
    assert s.entry_ref == dining


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


def test_resolved_duplicate_is_not_an_actual(conn, snapshot_id, seeder):
    # Issue #82: a copy resolved as a duplicate drops out of actuals and the
    # entry's last-seen date, even while still linked.
    rent = make_rent_entry(conn, snapshot_id)
    charge = seeder.add(date(2026, 5, 1), "-2000.00", "Landlord", "Housing")
    copy = seeder.add(date(2026, 5, 3), "-2000.00", "Landlord", "Housing")
    link_transaction(conn, snapshot_id, charge, rent)
    link_transaction(conn, snapshot_id, copy, rent)
    conn.execute(
        transaction_duplicates.insert().values(
            transaction_id=copy,
            duplicate_of_id=charge,
            reason="test",
            source="scan",
            status="duplicate",
        )
    )
    conn.commit()
    assert get_budget_link_date_ranges(conn, snapshot_id)[rent] == (
        date(2026, 5, 1),
        date(2026, 5, 1),
    )
    a = _by_ref(
        budget_actuals(conn, snapshot_id, year=2026, month=5, today=date(2026, 6, 1)),
        rent,
    )
    assert (a.count, a.actual) == (1, Decimal("2000.00"))


# --------------------------------------------------------------------------
# Suggestions sharpened by recurring series (issue #119)
# --------------------------------------------------------------------------

NOV = date(2026, 11, 20)


def _monthly_charges(seeder, merchant, amounts, category, day=10, start_month=5):
    """One charge a month from 2026-``start_month``, per amount; returns ids."""
    return [
        seeder.add(date(2026, start_month + i, day), f"-{a}", merchant, category)
        for i, a in enumerate(amounts)
    ]


def test_charge_in_linked_series_scores_high_despite_price_step(
    conn, snapshot_id, seeder
):
    # Netflix went from 15.49 to 22.99 — past the ±25% gate, so on amount alone
    # the new charge would never be proposed. Its series is linked to the entry.
    entry = make_expense_entry(
        conn, snapshot_id, amount=15.49, category="Entertainment", day_of_month=10
    )
    ids = _monthly_charges(
        seeder, "Netflix", ["15.49"] * 4 + ["22.99"] * 3, "Entertainment"
    )
    for txn_id in ids[:-1]:
        link_transaction(conn, snapshot_id, txn_id, entry)

    (s,) = suggest_links(conn, snapshot_id, today=NOV)
    assert s.transaction["id"] == ids[-1]
    assert s.entry_ref == entry
    assert s.confidence == "high"
    assert "recurring series linked here" in s.reasons
    assert "price drift" in s.reasons


def test_linked_series_picks_its_entry_over_a_lookalike(conn, snapshot_id, seeder):
    # Two same-priced Entertainment lines; the series is linked to the second.
    make_expense_entry(conn, snapshot_id, amount=11.99, category="Entertainment")
    spotify = make_expense_entry(
        conn, snapshot_id, amount=11.99, category="Entertainment"
    )
    ids = _monthly_charges(seeder, "Spotify", ["11.99"] * 6, "Entertainment")
    for txn_id in ids[:-1]:
        link_transaction(conn, snapshot_id, txn_id, spotify)
    (s,) = suggest_links(conn, snapshot_id, today=NOV)
    assert s.entry_ref == spotify


def test_newest_charge_of_unlinked_series_is_nudged(conn, snapshot_id, seeder):
    from tests.budget.conftest import make_entry

    entry = make_entry(
        conn, snapshot_id, description="Netflix", amount=15.49, recurrence="monthly"
    )
    ids = _monthly_charges(seeder, "Netflix", ["15.49"] * 6, "Entertainment")
    by_txn = {
        s.transaction["id"]: s for s in suggest_links(conn, snapshot_id, today=NOV)
    }
    newest = by_txn[ids[-1]]
    assert newest.entry_ref == entry
    assert "recurring series matches" in newest.reasons
    assert all("recurring series matches" not in by_txn[i].reasons for i in ids[:-1])
    assert newest.score > by_txn[ids[0]].score


def _amazon(seeder):
    # Irregular timing and amounts: a store, not a subscription.
    for d, a in [
        (date(2026, 6, 3), "42.10"),
        (date(2026, 6, 20), "88.00"),
        (date(2026, 8, 11), "23.50"),
    ]:
        seeder.add(d, f"-{a}", "Amazon", "Shopping")
    return seeder.add(date(2026, 10, 2), "-15.99", "Amazon", "Shopping")


def test_irregular_merchant_scores_lower_against_recurring_bill(
    conn, snapshot_id, seeder
):
    make_expense_entry(conn, snapshot_id, amount=15.99, category="Subscriptions")
    # A merchant seen once is unknown, not irregular: still suggested.
    seeder.add(date(2026, 10, 4), "-15.99", "Newco", "Shopping")
    _amazon(seeder)
    suggestions = suggest_links(conn, snapshot_id, today=NOV)
    assert [s.transaction["merchant"] for s in suggestions] == ["Newco"]

    # With a low floor the Amazon charge shows, scored down and explained.
    low = {
        s.transaction["merchant"]: s
        for s in suggest_links(conn, snapshot_id, today=NOV, min_score=0)
    }
    assert "merchant not recurring" in low["Amazon"].reasons
    assert low["Amazon"].score < low["Newco"].score


def test_irregular_penalty_skips_one_time_entries(conn, snapshot_id, seeder):
    from tests.budget.conftest import make_entry

    make_entry(
        conn, snapshot_id, amount=15.99, recurrence="one_time", date="2026-10-02"
    )
    txn_id = _amazon(seeder)
    (s,) = suggest_links(conn, snapshot_id, today=NOV)
    assert s.transaction["id"] == txn_id
    assert "merchant not recurring" not in s.reasons


def test_merchant_marked_not_recurring_is_irregular(conn, snapshot_id, seeder):
    from fintrack.budget.recurring import dismiss

    make_expense_entry(conn, snapshot_id, amount=15.99, category="Subscriptions")
    seeder.add(date(2026, 10, 4), "-15.99", "Newco", "Shopping")
    dismiss(conn, snapshot_id, "Newco")
    assert suggest_links(conn, snapshot_id, today=NOV) == []


def test_past_month_judged_by_its_own_history(conn, snapshot_id, seeder):
    # Viewing July: the series is detected as of July 31, so later charges
    # (and the later link) don't count.
    entry = make_expense_entry(
        conn, snapshot_id, amount=15.49, category="Entertainment", day_of_month=10
    )
    ids = _monthly_charges(
        seeder, "Netflix", ["15.49"] * 3 + ["22.99"] * 4, "Entertainment"
    )
    link_transaction(conn, snapshot_id, ids[-1], entry)
    # July (ids[2], 15.49) is unlinked; its window has no links → no bonus.
    (s,) = suggest_links(conn, snapshot_id, year=2026, month=7, today=NOV)
    assert s.transaction["id"] == ids[2]
    assert "recurring series linked here" not in s.reasons
