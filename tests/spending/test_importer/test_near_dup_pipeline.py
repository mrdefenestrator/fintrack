"""Near-duplicate flagging through run_import, and hiding resolved duplicates
from every confirmed-transaction read (issue #82)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from fintrack.accounts.balance_history import reconciliation_note, record_balance
from fintrack.core.models import transaction_duplicates, transactions
from fintrack.ledger.importer import run_import
from fintrack.ledger.repository.accounts import add_account, list_accounts
from fintrack.ledger.repository.aggregations import get_monthly_category_totals
from fintrack.ledger.repository.imports import confirm_import
from fintrack.ledger.repository.merchants import (
    list_merchants_with_stats,
    set_merchant_category,
)
from fintrack.ledger.repository.transactions import get_transactions


@pytest.fixture
def account_id(conn):
    return add_account(conn, name="Card", institution="Apple", account_type="checking")


def _flags(conn):
    rows = conn.execute(
        select(
            transaction_duplicates.c.transaction_id,
            transaction_duplicates.c.duplicate_of_id,
            transaction_duplicates.c.reason,
            transaction_duplicates.c.status,
            transaction_duplicates.c.source,
        ).order_by(transaction_duplicates.c.id)
    ).fetchall()
    return [tuple(r) for r in rows]


def _txn_id(conn, import_id, desc):
    return conn.execute(
        select(transactions.c.id).where(
            transactions.c.import_id == import_id,
            transactions.c.raw_description == desc,
        )
    ).scalar_one()


def test_reexport_with_new_ids_and_descriptions_is_flagged(conn, account_id, write_ofx):
    first = run_import(
        conn,
        write_ofx(
            [
                ("A1", "20261001", "-56.78", "UBER *TRIP HELP.UBER.COM"),
                ("A2", "20261002", "-9.99", "NETFLIX.COM"),
            ]
        ),
        account_id=account_id,
    )
    confirm_import(conn, first["import_id"])
    second = run_import(
        conn,
        write_ofx(
            [
                ("B1", "20261002", "-56.78", "Uber Trip Help.Uber.Com"),
                ("B2", "20261002", "-9.99", "Netflix.com"),
                ("B3", "20261003", "-20.00", "Shell Oil"),
            ]
        ),
        account_id=account_id,
    )

    assert second["flagged_count"] == 2
    assert second["new_count"] == 1
    flags = _flags(conn)
    assert [(t, d) for t, d, *_ in flags] == [
        (
            _txn_id(conn, second["import_id"], "Uber Trip Help.Uber.Com"),
            _txn_id(conn, first["import_id"], "UBER *TRIP HELP.UBER.COM"),
        ),
        (
            _txn_id(conn, second["import_id"], "Netflix.com"),
            _txn_id(conn, first["import_id"], "NETFLIX.COM"),
        ),
    ]
    assert flags[0][2].startswith("same amount · 1 day apart")
    assert {(status, source) for *_, status, source in flags} == {("flagged", "import")}


def test_row_already_matched_exactly_is_not_reused(conn, account_id, write_ofx):
    # The second file repeats the coffee exactly (skipped by fingerprint) and
    # adds a genuinely new same-price coffee the next day; that new one must
    # not be flagged against the original the exact match already accounts for.
    run_import(
        conn, write_ofx([("A1", "20261001", "-5.00", "COFFEE")]), account_id=account_id
    )
    second = run_import(
        conn,
        write_ofx(
            [
                ("A1", "20261001", "-5.00", "COFFEE"),
                ("A2", "20261002", "-5.00", "COFFEE"),
            ]
        ),
        account_id=account_id,
    )
    assert (second["skipped_count"], second["new_count"]) == (1, 1)
    assert second["flagged_count"] == 0


def test_same_day_repeat_still_flagged_against_its_sibling(conn, account_id, write_ofx):
    first = run_import(
        conn, write_ofx([("A1", "20261001", "-5.00", "COFFEE")]), account_id=account_id
    )
    second = run_import(
        conn,
        write_ofx(
            [
                ("A1", "20261001", "-5.00", "COFFEE"),
                ("A9", "20261001", "-5.00", "COFFEE"),
            ]
        ),
        account_id=account_id,
    )
    assert second["flagged_count"] == 1
    [(_, dup_of, reason, _, _)] = _flags(conn)
    assert dup_of == _txn_id(conn, first["import_id"], "COFFEE")
    assert reason == "repeat of an identical same-day charge already on file"


def test_flagged_rows_are_not_offered_as_originals(conn, account_id, write_ofx):
    # A third overlapping export pairs with the original, not the flagged copy.
    first = run_import(
        conn, write_ofx([("A1", "20261001", "-9.99", "NETFLIX")]), account_id=account_id
    )
    run_import(
        conn, write_ofx([("B1", "20261001", "-9.99", "Netflix")]), account_id=account_id
    )
    run_import(
        conn,
        write_ofx([("C1", "20261002", "-9.99", "Netflix.com")]),
        account_id=account_id,
    )
    original = _txn_id(conn, first["import_id"], "NETFLIX")
    assert [d for _, d, *_ in _flags(conn)] == [original, original]


# --- hiding resolved duplicates -------------------------------------------


@pytest.fixture
def resolved_copy(conn, account_id, write_ofx):
    """A confirmed original and a confirmed copy resolved as 'duplicate'."""
    first = run_import(
        conn,
        write_ofx([("A1", "20261001", "-56.78", "UBER *TRIP")]),
        account_id=account_id,
    )
    second = run_import(
        conn,
        write_ofx([("B1", "20261005", "-56.78", "Uber Trip")]),
        account_id=account_id,
    )
    # 4 days apart: outside the window, so flag it by hand as a scan would.
    copy_id = _txn_id(conn, second["import_id"], "Uber Trip")
    conn.execute(
        transaction_duplicates.insert().values(
            transaction_id=copy_id,
            duplicate_of_id=_txn_id(conn, first["import_id"], "UBER *TRIP"),
            reason="test",
            source="scan",
            status="flagged",
        )
    )
    for result in (first, second):
        confirm_import(conn, result["import_id"])
    return copy_id


def _resolve(conn, copy_id, status):
    conn.execute(
        update(transaction_duplicates)
        .where(transaction_duplicates.c.transaction_id == copy_id)
        .values(status=status)
    )
    conn.commit()


@pytest.mark.parametrize("status,visible", [("flagged", 2), ("duplicate", 1)])
def test_readers_skip_only_resolved_duplicates(conn, resolved_copy, status, visible):
    for name in set(conn.execute(select(transactions.c.normalized_merchant)).scalars()):
        set_merchant_category(conn, name, "Transport", source="manual")
    _resolve(conn, resolved_copy, status)
    assert len(get_transactions(conn)) == visible
    [totals] = get_monthly_category_totals(conn, year=2026, month=10)
    assert totals["count"] == visible
    assert sum(m["txn_count"] for m in list_merchants_with_stats(conn)) == visible


def test_latest_transaction_date_ignores_duplicates(conn, account_id, resolved_copy):
    _resolve(conn, resolved_copy, "duplicate")
    [acct] = [a for a in list_accounts(conn) if a["id"] == account_id]
    assert acct["latest_txn_date"] == date(2026, 10, 1)


def test_balance_reconciliation_ignores_duplicates(conn, account_id, resolved_copy):
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("100.00"),
        as_of=date(2026, 9, 30),
        source="manual",
    )
    _resolve(conn, resolved_copy, "duplicate")
    # 100 - 56.78 once, not twice.
    assert (
        reconciliation_note(
            conn,
            account_id=account_id,
            statement_balance=Decimal("43.22"),
            as_of=date(2026, 10, 31),
        )
        is None
    )
