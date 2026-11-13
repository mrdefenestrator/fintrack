"""Budget history (issue #136): an entry's start/end months and dated versions
of its amount and schedule, and how every reader sees the month's own plan."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from fintrack.budget import reconcile
from fintrack.budget.recurrence import (
    budget_entry_in_month,
    entry_active,
    entry_for_month,
    subtotal_remainder_of_month,
)
from fintrack.budget.repository import (
    budgeted_monthly_by_category,
    delete_budget_entry,
    delete_budget_version,
    end_budget_entry,
    entry_versions,
    get_budget_entries,
    get_budget_entry,
    set_budget_entry_span,
    update_budget_entry,
    update_budget_version,
)
from fintrack.core.models import budget_entry_versions
from fintrack.snapshots.repository import copy_snapshot
from tests.budget.conftest import make_entry

JAN, MAR, MAY, JUL, SEP = (date(2024, m, 1) for m in (1, 3, 5, 7, 9))


def _rent(conn, snapshot_id, as_of):
    return get_budget_entries(conn, snapshot_id, as_of=as_of)[0]


def _amounts(conn, snapshot_id, months):
    return [_rent(conn, snapshot_id, m)["amount"] for m in months]


def _version_months(conn, entry_id):
    return [
        r.effective_from
        for r in conn.execute(
            select(budget_entry_versions.c.effective_from)
            .where(budget_entry_versions.c.entry_id == entry_id)
            .order_by(budget_entry_versions.c.effective_from)
        )
    ]


@pytest.fixture
def rent(conn, snapshot_id):
    """A Rent entry from before #136: no start, no end, $2000 monthly."""
    return make_entry(conn, snapshot_id, description="Rent", amount=2000)


# ---------------------------------------------------------------------------
# Recurrence engine (pure)
# ---------------------------------------------------------------------------

_VERSIONED = {
    "kind": "expense",
    "description": "Rent",
    "amount": Decimal(2100),
    "recurrence": "monthly",
    "startMonth": "2024-01-01",
    "endMonth": "2024-08-01",
    "_versions": [
        {"from": None, "amount": Decimal(2000), "recurrence": "monthly"},
        {"from": "2024-05-01", "amount": Decimal(2100), "recurrence": "monthly"},
    ],
}


def test_entry_for_month_picks_the_version_in_effect():
    assert entry_for_month(_VERSIONED, 2024, 4)["amount"] == 2000
    assert entry_for_month(_VERSIONED, 2024, 5)["amount"] == 2100
    assert entry_for_month(_VERSIONED, 2024, 8)["amount"] == 2100


def test_months_outside_the_span_are_not_planned():
    assert not entry_active(_VERSIONED, 2023, 12)
    assert entry_active(_VERSIONED, 2024, 1)
    assert entry_active(_VERSIONED, 2024, 8)
    assert not entry_active(_VERSIONED, 2024, 9)
    assert entry_for_month(_VERSIONED, 2024, 9) is None
    assert budget_entry_in_month(_VERSIONED, 2023, 12) == 0
    assert budget_entry_in_month(_VERSIONED, 2024, 9) == 0
    assert subtotal_remainder_of_month(_VERSIONED, 2024, 9, 0) == 0


def test_per_month_amounts_follow_each_months_version():
    """Multi-month readers (projections, cash flow) get each month's plan,
    whatever version the entry's flat fields hold."""
    assert budget_entry_in_month(_VERSIONED, 2024, 3) == 2000
    assert budget_entry_in_month(_VERSIONED, 2024, 6) == 2100
    assert subtotal_remainder_of_month(_VERSIONED, 2024, 3, 0) == 2000


def test_entries_without_history_are_unchanged():
    plain = {"kind": "expense", "amount": 50, "recurrence": "monthly"}
    assert entry_for_month(plain, 1999, 1) is plain
    assert budget_entry_in_month(plain, 1999, 1) == 50


# ---------------------------------------------------------------------------
# Repository: editing splits the version covering the month
# ---------------------------------------------------------------------------


def test_edit_applies_from_its_month_until_the_next_change(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    assert _amounts(conn, snapshot_id, [MAR, JUL, SEP]) == [2000, 2100, 2100]

    # A later edit in an earlier month stops at the July change.
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2050}, effective=MAR)
    assert _amounts(conn, snapshot_id, [JAN, MAR, MAY, JUL]) == [2000, 2050, 2050, 2100]
    assert _version_months(conn, rent) == [MAR, JUL]


def test_editing_a_versions_own_month_changes_it_in_place(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2200}, effective=JUL)
    assert _version_months(conn, rent) == [JUL]
    assert _amounts(conn, snapshot_id, [MAR, JUL]) == [2000, 2200]


def test_an_entry_edited_in_its_start_month_has_no_versions(conn, snapshot_id):
    make_entry(conn, snapshot_id, description="Gym", amount=40, startMonth="2024-03-01")
    update_budget_entry(conn, snapshot_id, 0, {"amount": 45}, effective=MAR)
    entry = get_budget_entries(conn, snapshot_id, as_of=MAR)[0]
    assert entry["amount"] == 45
    assert "_versions" not in entry


def test_changing_back_drops_the_redundant_version(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2000}, effective=JUL)
    assert _version_months(conn, rent) == []


def test_unversioned_fields_apply_to_every_month(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    update_budget_entry(
        conn,
        snapshot_id,
        0,
        {"description": "Lease", "category": "Housing"},
        effective=JUL,
    )
    for month in (MAR, JUL):
        entry = _rent(conn, snapshot_id, month)
        assert (entry["description"], entry["category"]) == ("Lease", "Housing")


def test_schedule_fields_are_versioned(conn, snapshot_id, rent):
    update_budget_entry(
        conn,
        snapshot_id,
        0,
        {"recurrence": "annual", "month": 9, "dayOfMonth": 1},
        effective=JUL,
    )
    assert _rent(conn, snapshot_id, MAR)["recurrence"] == "monthly"
    assert "month" not in _rent(conn, snapshot_id, MAR)
    assert _rent(conn, snapshot_id, JUL)["recurrence"] == "annual"
    entry = _rent(conn, snapshot_id, JUL)
    assert budget_entry_in_month(entry, 2024, 3) == 2000  # March: still monthly
    assert budget_entry_in_month(entry, 2024, 8) == 0  # annual, due in September
    assert budget_entry_in_month(entry, 2024, 9) == 2000


def test_correcting_a_version_in_place(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    update_budget_version(conn, snapshot_id, 0, JUL, {"amount": 2150})
    update_budget_version(conn, snapshot_id, 0, None, {"amount": 1950})
    assert _amounts(conn, snapshot_id, [MAR, JUL]) == [1950, 2150]
    with pytest.raises(ValueError):
        update_budget_version(conn, snapshot_id, 0, MAY, {"amount": 1})
    with pytest.raises(ValueError):
        update_budget_version(conn, snapshot_id, 0, None, {"description": "x"})


def test_removing_a_version_lets_the_one_before_run_on(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=MAY)
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2200}, effective=SEP)
    delete_budget_version(conn, snapshot_id, 0, MAY)
    assert _amounts(conn, snapshot_id, [MAR, MAY, SEP]) == [2000, 2000, 2200]
    with pytest.raises(ValueError):
        delete_budget_version(conn, snapshot_id, 0, MAY)


def test_entry_versions_lists_the_history(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    versions = entry_versions(_rent(conn, snapshot_id, MAR))
    assert [(v["from"], v["amount"]) for v in versions] == [
        (None, 2000),
        ("2024-07-01", 2100),
    ]
    # The flat fields are as of the month asked for; the history is the same.
    assert entry_versions(_rent(conn, snapshot_id, SEP)) == versions


# ---------------------------------------------------------------------------
# Start / end
# ---------------------------------------------------------------------------


def test_end_keeps_earlier_months_and_drops_later_changes(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=SEP)
    end_budget_entry(conn, snapshot_id, 0, MAY)
    entry = _rent(conn, snapshot_id, MAY)
    assert entry["endMonth"] == "2024-05-01"
    assert entry_active(entry, 2024, 5)
    assert not entry_active(entry, 2024, 6)
    assert _version_months(conn, rent) == []

    end_budget_entry(conn, snapshot_id, 0, None)  # resume
    assert "endMonth" not in _rent(conn, snapshot_id, MAY)


def test_moving_the_start_past_a_change_folds_it_into_the_base(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=MAR)
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2200}, effective=SEP)
    set_budget_entry_span(conn, snapshot_id, 0, start=MAY)
    assert _version_months(conn, rent) == [SEP]
    assert _amounts(conn, snapshot_id, [MAY, SEP]) == [2100, 2200]
    assert entry_versions(_rent(conn, snapshot_id, MAY))[0]["from"] == "2024-05-01"


def test_span_rejects_an_end_before_the_start(conn, snapshot_id, rent):
    set_budget_entry_span(conn, snapshot_id, 0, start=MAY)
    with pytest.raises(ValueError):
        end_budget_entry(conn, snapshot_id, 0, MAR)


def test_deleting_an_entry_deletes_its_versions(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    delete_budget_entry(conn, snapshot_id, 0)
    assert conn.execute(select(budget_entry_versions)).first() is None


def test_get_budget_entry_reads_as_of_a_month(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    assert get_budget_entry(conn, snapshot_id, rent, as_of=MAR)["amount"] == 2000
    assert get_budget_entry(conn, snapshot_id, rent, as_of=JUL)["amount"] == 2100


def test_copying_a_snapshot_copies_the_history(conn, snapshot_id, rent):
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)
    end_budget_entry(conn, snapshot_id, 0, SEP)
    copy_id = copy_snapshot(conn, snapshot_id, "copy")
    copied = get_budget_entries(conn, copy_id, as_of=MAR)[0]
    assert copied["amount"] == 2000
    assert copied["endMonth"] == "2024-09-01"
    assert get_budget_entries(conn, copy_id, as_of=JUL)[0]["amount"] == 2100


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def test_budget_per_month_averages_each_months_plan(conn, snapshot_id):
    make_entry(conn, snapshot_id, amount=100, category="Housing")
    update_budget_entry(conn, snapshot_id, 0, {"amount": 300}, effective=MAR)
    budget = get_budget_entries(conn, snapshot_id, as_of=MAR)
    totals, _ = budgeted_monthly_by_category(budget, [(2024, 1), (2024, 2), (2024, 3)])
    assert totals["Housing"] == pytest.approx(Decimal(500) / 3)
    totals, _ = budgeted_monthly_by_category(budget, [(2024, 3)])
    assert totals["Housing"] == 300


def test_budget_per_month_counts_only_planned_months(conn, snapshot_id):
    make_entry(conn, snapshot_id, amount=120, category="Gym", startMonth="2024-03-01")
    budget = get_budget_entries(conn, snapshot_id, as_of=MAR)
    totals, _ = budgeted_monthly_by_category(budget, [(2024, m) for m in (1, 2, 3, 4)])
    assert totals["Gym"] == 60  # in the plan for 2 of the 4 months
    totals, _ = budgeted_monthly_by_category(budget, [(2024, 1)])
    assert "Gym" not in totals


def test_budget_actuals_use_the_months_version(conn, snapshot_id, seeder, rent):
    for day, amount in ((date(2024, 3, 1), "-2000.00"), (date(2024, 7, 1), "-2100.00")):
        txn = seeder.add(day, amount, "Landlord", "Housing")
        reconcile.link_transaction(conn, snapshot_id, txn, rent)
    update_budget_entry(conn, snapshot_id, 0, {"amount": 2100}, effective=JUL)

    today = date(2024, 9, 15)
    (march,) = reconcile.budget_actuals(
        conn, snapshot_id, year=2024, month=3, today=today
    )
    (july,) = reconcile.budget_actuals(
        conn, snapshot_id, year=2024, month=7, today=today
    )
    # No phantom drift: each month is measured against the price it had.
    assert (march.expected, march.drift_amount, march.status) == (2000, 0, "matched")
    assert (july.expected, july.drift_amount, july.status) == (2100, 0, "matched")


def test_budget_actuals_leave_out_entries_not_in_the_plan(conn, snapshot_id, rent):
    end_budget_entry(conn, snapshot_id, 0, MAR)
    today = date(2024, 9, 15)
    assert reconcile.budget_actuals(conn, snapshot_id, year=2024, month=3, today=today)
    assert not reconcile.budget_actuals(
        conn, snapshot_id, year=2024, month=4, today=today
    )
