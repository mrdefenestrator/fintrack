"""Recurring charges not in the budget (issue #100): pattern detection over
made-up series (steady, drifting day, price change, irregular), matching
against the budget, and the add-to-budget / dismiss actions."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fintrack.budget import recurring
from fintrack.budget.recurring import (
    NotRecurring,
    add_to_budget,
    attention_items,
    detect_pattern,
    dismiss,
    find_recurring,
    link_to_entry,
    next_occurrence,
    undismiss,
)
from fintrack.budget.repository import get_budget_entries
from fintrack.ledger.repository.corrections import get_correction
from fintrack.snapshots.repository import create_snapshot

from .conftest import make_entry

TODAY = date(2026, 11, 9)
D = Decimal


def monthly(day, months, amount="15.49", start=(2026, 1)):
    """``months`` charges on ``day`` (clamped), from ``start`` (year, month)."""
    year, month = start
    out = []
    for _ in range(months):
        out.append((date(year, month, min(day, 28)), D(amount)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


# --------------------------------------------------------------------------
# detect_pattern: made-up series
# --------------------------------------------------------------------------


def test_steady_monthly():
    p = detect_pattern(monthly(5, 10, start=(2026, 2)), TODAY)
    assert p.cadence.key == "monthly"
    assert p.amount == D("15.49")
    assert p.count == 10
    assert p.last_date == date(2026, 11, 5)
    assert p.next_date == date(2026, 12, 5)
    assert p.price_change_since is None


def test_drifting_day_still_monthly():
    # Posts anywhere from the 1st to the 6th, or the last days of the month
    # before (a weekend shift).
    days = [
        date(2026, 5, 3),
        date(2026, 5, 30),
        date(2026, 7, 6),
        date(2026, 8, 1),
        date(2026, 9, 4),
        date(2026, 10, 2),
        date(2026, 11, 3),
    ]
    p = detect_pattern([(d, D("60.00")) for d in days], TODAY)
    assert p is not None and p.cadence.key == "monthly"


def test_similar_amounts_count_as_one_price():
    # A utility within 10%: still recurring, typical is the median.
    amounts = ["50.00", "52.10", "48.90", "51.00", "49.50"]
    charges = [(d, D(a)) for (d, _), a in zip(monthly(12, 5, start=(2026, 7)), amounts)]
    p = detect_pattern(charges, TODAY)
    assert p.amount == D("50.00")
    assert p.price_change_since is None


def test_price_change_is_reported():
    charges = monthly(20, 6, "15.49", start=(2026, 3)) + monthly(
        20, 2, "17.99", start=(2026, 9)
    )
    p = detect_pattern(charges, TODAY)
    assert p.amount == D("17.99")
    assert p.previous_amount == D("15.49")
    assert p.price_change_since == date(2026, 9, 20)


def test_old_price_change_is_not_news():
    charges = monthly(20, 3, "15.49", start=(2026, 1)) + monthly(
        20, 7, "17.99", start=(2026, 4)
    )
    p = detect_pattern(charges, TODAY)
    assert p.amount == D("17.99")
    assert p.price_change_since is None


def test_irregular_timing_is_not_recurring():
    days = [
        date(2026, 6, 1),
        date(2026, 6, 9),
        date(2026, 7, 20),
        date(2026, 8, 2),
        date(2026, 9, 29),
        date(2026, 10, 30),
    ]
    assert detect_pattern([(d, D("15.49")) for d in days], TODAY) is None


def test_irregular_amounts_are_not_recurring():
    # Weekly groceries: steady timing, but no steady price.
    amounts = ["82.10", "45.33", "120.00", "61.75", "99.10", "33.20"]
    start = date(2026, 9, 28)
    charges = [(start + timedelta(weeks=i), D(a)) for i, a in enumerate(amounts)]
    assert detect_pattern(charges, TODAY) is None


def test_too_few_charges():
    assert detect_pattern(monthly(5, 2, start=(2026, 9)), TODAY) is None


def test_stale_series_is_dropped():
    # Last charge in June: probably cancelled.
    assert detect_pattern(monthly(5, 5, start=(2026, 2)), TODAY) is None


def test_weekly_and_biweekly():
    start = date(2026, 10, 5)
    weekly = [(start + timedelta(weeks=i), D("6.00")) for i in range(5)]
    assert detect_pattern(weekly, TODAY).cadence.key == "weekly"
    start = date(2026, 8, 7)
    biweekly = [(start + timedelta(weeks=2 * i), D("2000")) for i in range(7)]
    p = detect_pattern(biweekly, TODAY)
    assert p.cadence.key == "biweekly"
    assert p.next_date == date(2026, 11, 13)


def test_yearly_needs_only_two():
    p = detect_pattern(
        [(date(2025, 11, 1), D("99")), (date(2026, 11, 2), D("99"))], TODAY
    )
    assert p.cadence.key == "annual"
    assert p.next_date == date(2027, 11, 2)


def test_two_charges_in_one_period_is_not_a_subscription():
    charges = monthly(5, 5, start=(2026, 7)) + [(date(2026, 9, 9), D("15.49"))]
    assert detect_pattern(charges, TODAY) is None


def test_next_occurrence_clamps_to_month_end():
    monthly_cadence = recurring.CADENCE_BY_KEY["monthly"]
    assert next_occurrence(date(2026, 1, 31), monthly_cadence) == date(2026, 2, 28)
    assert next_occurrence(date(2026, 12, 15), monthly_cadence) == date(2027, 1, 15)


# --------------------------------------------------------------------------
# find_recurring: against the budget
# --------------------------------------------------------------------------


def seed_monthly(seeder, merchant, amount, category, months=6, day=10, start=(2026, 5)):
    return [
        seeder.add(d, f"-{a}", merchant, category)
        for d, a in monthly(day, months, amount, start=start)
    ]


def test_unbudgeted_series_is_found(conn, snapshot_id, seeder):
    seed_monthly(seeder, "Spotify", "11.99", "Entertainment")
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.merchant, c.kind, c.status) == ("Spotify", "expense", "not_budgeted")
    assert c.amount == D("-11.99")
    assert c.category == "Entertainment"
    assert c.needs_attention
    assert attention_items([c]) == [c]


def test_transfers_are_excluded_and_income_is_separate(conn, snapshot_id, seeder):
    seed_monthly(seeder, "To Savings", "500.00", "Transfer")
    for d, a in monthly(1, 6, "3000.00", start=(2026, 6)):
        seeder.add(d, a, "Employer", "Income")
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.merchant, c.kind, c.amount) == ("Employer", "income", D("3000.00"))


def test_linked_series(conn, snapshot_id, seeder):
    from fintrack.budget.reconcile import link_transaction

    entry = make_entry(conn, snapshot_id, description="Music", amount=11.99)
    ids = seed_monthly(seeder, "Spotify", "11.99", "Entertainment")
    link_transaction(conn, snapshot_id, ids[0], entry)
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.status, c.entry_ref, c.entry_description) == ("linked", entry, "Music")
    assert not c.needs_attention


def test_entry_by_name_is_unlinked_match(conn, snapshot_id, seeder):
    entry = make_entry(conn, snapshot_id, description="Netflix plan", amount=20)
    seed_monthly(seeder, "Netflix", "15.49", "Entertainment")
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.status, c.entry_ref) == ("unlinked", entry)
    assert link_to_entry(conn, snapshot_id, "Netflix", entry, today=TODAY) == 6
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert c.status == "linked"


def test_entry_by_category_and_price_is_unlinked_match(conn, snapshot_id, seeder):
    entry = make_entry(
        conn, snapshot_id, description="Gym", amount=40, category="Health"
    )
    seed_monthly(seeder, "PLANET FIT", "40.00", "Health")
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.status, c.entry_ref) == ("unlinked", entry)


def test_category_measured_line_covers_it(conn, snapshot_id, seeder):
    entry = make_entry(
        conn,
        snapshot_id,
        description="Coffee",
        amount=80,
        category="Dining Out",
        continuous=True,
    )
    seed_monthly(seeder, "Corner Cafe", "5.25", "Dining Out")
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert (c.status, c.entry_ref) == ("covered", entry)


def test_price_change_needs_attention_even_when_linked(conn, snapshot_id, seeder):
    from fintrack.budget.reconcile import link_transaction

    entry = make_entry(conn, snapshot_id, description="Netflix", amount=15.49)
    ids = seed_monthly(seeder, "Netflix", "15.49", "Entertainment", months=4)
    ids += seed_monthly(
        seeder, "Netflix", "17.99", "Entertainment", months=2, start=(2026, 9)
    )
    link_transaction(conn, snapshot_id, ids[0], entry)
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert c.status == "linked"
    assert c.price_changed and c.needs_attention
    assert (c.pattern.previous_amount, c.pattern.amount) == (D("15.49"), D("17.99"))


def test_scoped_to_snapshot(conn, snapshot_id, seeder):
    seed_monthly(seeder, "Spotify", "11.99", "Entertainment")
    other = create_snapshot(conn, "other")
    assert find_recurring(conn, other, today=TODAY) == []


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


def test_add_to_budget_creates_entry_and_links(conn, snapshot_id, seeder, account):
    ids = seed_monthly(seeder, "Spotify", "11.99", "Entertainment", day=14)
    ref = add_to_budget(conn, snapshot_id, "Spotify", today=TODAY)
    [entry] = get_budget_entries(conn, snapshot_id)
    assert entry["_db_id"] == ref
    assert entry["kind"] == "expense"
    assert entry["description"] == "Spotify"
    assert entry["amount"] == D("11.99")
    assert entry["recurrence"] == "monthly"
    assert entry["dayOfMonth"] == 14
    assert entry["category"] == "Entertainment"
    assert entry["autoAccountRef"] == account
    assert all(get_correction(conn, i)["budget_entry_ref"] == ref for i in ids)
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert c.status == "linked"


def test_add_weekly_and_annual(conn, snapshot_id, seeder):
    start = date(2026, 10, 5)
    for i in range(5):
        seeder.add(start + timedelta(weeks=i), "-6.00", "Car Wash", "Auto")
    seeder.add(date(2025, 11, 3), "-99.00", "Domain Co", "Software")
    seeder.add(date(2026, 11, 3), "-99.00", "Domain Co", "Software")
    add_to_budget(conn, snapshot_id, "Car Wash", today=TODAY)
    add_to_budget(conn, snapshot_id, "Domain Co", today=TODAY)
    wash, domain = get_budget_entries(conn, snapshot_id)
    assert wash["recurrence"] == "weekly" and "dayOfMonth" not in wash
    assert (domain["recurrence"], domain["month"], domain["dayOfMonth"]) == (
        "annual",
        11,
        3,
    )


def test_add_unknown_merchant_raises(conn, snapshot_id):
    with pytest.raises(NotRecurring):
        add_to_budget(conn, snapshot_id, "Nobody", today=TODAY)


def test_dismiss_and_undismiss(conn, snapshot_id, seeder):
    seed_monthly(seeder, "Spotify", "11.99", "Entertainment")
    dismiss(conn, snapshot_id, "Spotify")
    dismiss(conn, snapshot_id, "Spotify")  # idempotent
    assert find_recurring(conn, snapshot_id, today=TODAY) == []
    assert (
        len(find_recurring(conn, snapshot_id, today=TODAY, include_dismissed=True)) == 1
    )
    # Per snapshot: another household's mark doesn't hide it.
    other = create_snapshot(conn, "other")
    assert not undismiss(conn, other, "Spotify")
    assert undismiss(conn, snapshot_id, "Spotify")
    assert len(find_recurring(conn, snapshot_id, today=TODAY)) == 1


def test_hidden_duplicates_dont_count(conn, snapshot_id, seeder):
    from sqlalchemy import insert

    from fintrack.core.models import transaction_duplicates

    ids = seed_monthly(
        seeder, "Spotify", "11.99", "Entertainment", months=3, start=(2026, 8)
    )
    # A fourth row in the same month, resolved as a duplicate: without the
    # filter it would make two charges in one period (not recurring).
    dup = seeder.add(date(2026, 9, 11), "-11.99", "Spotify", "Entertainment")
    conn.execute(
        insert(transaction_duplicates).values(
            transaction_id=dup,
            duplicate_of_id=ids[1],
            status="duplicate",
            reason="test",
            source="scan",
        )
    )
    conn.commit()
    [c] = find_recurring(conn, snapshot_id, today=TODAY)
    assert c.pattern.count == 3


def test_summary_lines(conn, snapshot_id, seeder):
    seed_monthly(seeder, "Spotify", "11.99", "Entertainment")
    seed_monthly(seeder, "Netflix", "15.49", "Entertainment", months=4)
    seed_monthly(seeder, "Netflix", "17.99", "Entertainment", months=2, start=(2026, 9))
    lines = {
        c.merchant: recurring.summary_line(c)
        for c in find_recurring(conn, snapshot_id, today=TODAY)
    }
    assert lines == {
        "Spotify": "Spotify: $11.99/mo, not in budget",
        "Netflix": "Netflix: $15.49 → $17.99 since Sep",
    }
