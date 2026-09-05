from datetime import date
from decimal import Decimal

from sqlalchemy import select

from fintrack.accounts.balance_history import (
    get_balance_history,
    latest_point,
    reconciliation_note,
    record_balance,
)
from fintrack.accounts.repository import add_account as fin_add_account
from fintrack.accounts.repository import update_account
from fintrack.core.models import balance_history
from fintrack.ledger.repository.accounts import add_account, get_account_by_id
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.snapshots.repository import create_snapshot


def _account(conn, name="Checking"):
    snapshot_id = create_snapshot(conn, f"snap-{name}")
    account_id = add_account(
        conn,
        name=name,
        institution="Bank",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    return snapshot_id, account_id


def test_record_balance_inserts_and_resyncs_account(conn):
    _, account_id = _account(conn)
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("123.45"),
        as_of=date(2026, 6, 1),
        source="statement",
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("123.45")
    assert acct["as_of_date"] == date(2026, 6, 1)


def test_record_balance_upserts_same_day_same_source(conn):
    _, account_id = _account(conn)
    record_balance(
        conn, account_id=account_id, balance=Decimal(10), as_of=date(2026, 6, 1)
    )
    record_balance(
        conn, account_id=account_id, balance=Decimal(20), as_of=date(2026, 6, 1)
    )
    rows = get_balance_history(conn, account_id)
    assert len(rows) == 1
    assert rows[0]["balance"] == Decimal(20)


def test_manual_and_statement_same_day_coexist(conn):
    _, account_id = _account(conn)
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal(10),
        as_of=date(2026, 6, 1),
        source="statement",
    )
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal(15),
        as_of=date(2026, 6, 1),
        source="manual",
    )
    rows = get_balance_history(conn, account_id)
    assert len(rows) == 2
    # newest write wins the account re-sync (id tiebreak on equal as_of)
    assert get_account_by_id(conn, account_id)["balance"] == Decimal(15)


def test_history_ordering_and_limit(conn):
    _, account_id = _account(conn)
    for day, bal in ((3, "30"), (1, "10"), (2, "20")):
        record_balance(
            conn,
            account_id=account_id,
            balance=Decimal(bal),
            as_of=date(2026, 6, day),
        )
    rows = get_balance_history(conn, account_id)
    assert [r["balance"] for r in rows] == [Decimal(b) for b in ("10", "20", "30")]
    assert [r["balance"] for r in get_balance_history(conn, account_id, limit=2)] == [
        Decimal(20),
        Decimal(30),
    ]
    assert latest_point(conn, account_id)["balance"] == Decimal(30)
    # account synced to the newest as_of, not the last write
    assert get_account_by_id(conn, account_id)["balance"] == Decimal(30)


def test_manual_balance_edit_writes_history(conn):
    snapshot_id = create_snapshot(conn, "manual-edit")
    account_id = fin_add_account(
        conn, snapshot_id, {"name": "Cash", "type": "wallet", "balance": Decimal(50)}
    )
    # creation with a balance seeds the first point
    assert len(get_balance_history(conn, account_id)) == 1

    update_account(conn, snapshot_id, account_id, {"balance": Decimal(75)})
    rows = get_balance_history(conn, account_id)
    assert rows[-1]["balance"] == Decimal(75)
    assert rows[-1]["source"] == "manual"
    assert get_account_by_id(conn, account_id)["balance"] == Decimal(75)

    # a non-balance edit adds no point
    update_account(conn, snapshot_id, account_id, {"name": "Cash 2"})
    assert len(get_balance_history(conn, account_id)) == len(rows)


def test_manual_balance_edit_stamps_client_today(conn):
    """A caller-supplied `today` (the browser's local date, QA #6) dates the new
    balance-history point instead of the server's local date."""
    snapshot_id = create_snapshot(conn, "client-today")
    account_id = fin_add_account(
        conn,
        snapshot_id,
        {
            "name": "Cash",
            "type": "wallet",
            "balance": Decimal(50),
            "asOfDate": date(2026, 1, 1),
        },
    )
    update_account(
        conn,
        snapshot_id,
        account_id,
        {"balance": Decimal(75)},
        today=date(2026, 6, 5),
    )
    point = latest_point(conn, account_id)
    assert point["as_of"] == date(2026, 6, 5)
    assert point["balance"] == Decimal(75)
    assert get_account_by_id(conn, account_id)["as_of_date"] == date(2026, 6, 5)


def test_cc_available_edit_derives_balance_and_writes_history(conn):
    snapshot_id = create_snapshot(conn, "cc-edit")
    account_id = fin_add_account(
        conn,
        snapshot_id,
        {
            "name": "Visa",
            "type": "credit_card",
            "limit": Decimal(5000),
            "available": Decimal(5000),
        },
    )
    update_account(conn, snapshot_id, account_id, {"available": Decimal(4400)})
    point = latest_point(conn, account_id)
    assert point["balance"] == Decimal(-600)
    assert point["source"] == "manual"


def test_confirm_import_records_statement_point(conn):
    _, account_id = _account(conn, name="Stmt")
    import_id = create_import(
        conn,
        account_id=account_id,
        filename="jan.ofx",
        file_hash="h1",
        ledger_balance=Decimal("987.65"),
        ledger_balance_date=date(2026, 6, 15),
        available_balance=Decimal("900.00"),
    )
    confirm_import(conn, import_id)

    point = latest_point(conn, account_id)
    assert point["source"] == "statement"
    assert point["balance"] == Decimal("987.65")
    assert point["available"] == Decimal("900.00")
    assert point["import_id"] == import_id
    assert point["note"] is None  # nothing earlier to reconcile against
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("987.65")
    assert acct["as_of_date"] == date(2026, 6, 15)


def test_confirm_import_without_balance_records_nothing(conn):
    _, account_id = _account(conn, name="NoBal")
    import_id = create_import(
        conn, account_id=account_id, filename="x.csv", file_hash="h2"
    )
    confirm_import(conn, import_id)
    assert conn.execute(select(balance_history)).first() is None


def test_reconciliation_matches_when_transactions_explain_delta(conn):
    _, account_id = _account(conn, name="Recon")
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("100.00"),
        as_of=date(2026, 6, 1),
        source="statement",
    )
    import_id = create_import(
        conn,
        account_id=account_id,
        filename="feb.ofx",
        file_hash="h3",
        ledger_balance=Decimal("70.00"),
        ledger_balance_date=date(2026, 6, 30),
    )
    insert_transactions(
        conn,
        import_id=import_id,
        account_id=account_id,
        transactions_data=[
            {
                "date": date(2026, 6, 10),
                "amount": Decimal("-30.00"),
                "raw_description": "SHOP",
                "normalized_merchant": "SHOP",
                "fingerprint": "fp1",
            }
        ],
    )
    confirm_import(conn, import_id)
    assert latest_point(conn, account_id)["note"] is None


def test_reconciliation_flags_unexplained_delta(conn):
    _, account_id = _account(conn, name="Recon2")
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("100.00"),
        as_of=date(2026, 6, 1),
        source="statement",
    )
    note = reconciliation_note(
        conn,
        account_id=account_id,
        statement_balance=Decimal("120.00"),
        as_of=date(2026, 6, 30),
    )
    assert note is not None
    assert "unreconciled" in note
    assert "+20.00" in note


# ---- credit-card available/limit consistency on sync -------------------------


def _cc_account(conn, name="Visa", credit_limit=None):
    snapshot_id = create_snapshot(conn, f"snap-{name}")
    account = {"name": name, "type": "credit_card"}
    if credit_limit is not None:
        account["limit"] = credit_limit
    account_id = fin_add_account(conn, snapshot_id, account)
    return snapshot_id, account_id


def test_cc_import_confirm_updates_available(conn):
    _, account_id = _cc_account(conn, name="CCImport", credit_limit=Decimal(5000))
    import_id = create_import(
        conn,
        account_id=account_id,
        filename="cc.ofx",
        file_hash="cc1",
        ledger_balance=Decimal("-600.00"),
        ledger_balance_date=date(2026, 6, 15),
        available_balance=Decimal("4400.00"),
    )
    confirm_import(conn, import_id)
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("-600.00")
    assert acct["available"] == Decimal("4400.00")
    assert acct["credit_limit"] == Decimal(5000)
    # invariant restored: balance == available - credit_limit
    assert acct["balance"] == acct["available"] - acct["credit_limit"]


def test_cc_sync_derives_available_when_point_lacks_it(conn):
    _, account_id = _cc_account(conn, name="CCDerive", credit_limit=Decimal(5000))
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("-600.00"),
        as_of=date(2026, 6, 15),
        source="statement",
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("-600.00")
    assert acct["available"] == Decimal("4400.00")  # credit_limit + balance
    assert acct["credit_limit"] == Decimal(5000)


def test_cc_sync_fills_null_credit_limit_from_available(conn):
    _, account_id = _cc_account(conn, name="CCFill")  # no credit_limit
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("-600.00"),
        as_of=date(2026, 6, 15),
        source="statement",
        available=Decimal("4400.00"),
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["available"] == Decimal("4400.00")
    assert acct["credit_limit"] == Decimal("5000.00")  # available - balance


def test_cc_sync_never_overwrites_user_credit_limit(conn):
    _, account_id = _cc_account(conn, name="CCKeep", credit_limit=Decimal(5000))
    # The user-set credit_limit must survive a statement import. available is
    # no longer stored (schema split D4): it is computed as credit_limit +
    # balance, so the statement's reported available (4000, which would embed
    # pending holds) is deliberately discarded in favor of 5000 + (-600).
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("-600.00"),
        as_of=date(2026, 6, 15),
        source="statement",
        available=Decimal("4000.00"),
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["credit_limit"] == Decimal(5000)
    assert acct["available"] == Decimal("4400.00")  # computed: limit + balance


def test_cc_sync_no_available_no_limit_leaves_available_null(conn):
    _, account_id = _cc_account(conn, name="CCBare")  # no credit_limit
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("-600.00"),
        as_of=date(2026, 6, 15),
        source="statement",
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("-600.00")
    assert acct["available"] is None
    assert acct["credit_limit"] is None


def test_non_cc_sync_leaves_available_and_limit_untouched(conn):
    _, account_id = _account(conn, name="CheckingSync")
    record_balance(
        conn,
        account_id=account_id,
        balance=Decimal("987.65"),
        as_of=date(2026, 6, 15),
        source="statement",
        available=Decimal("900.00"),
    )
    acct = get_account_by_id(conn, account_id)
    assert acct["balance"] == Decimal("987.65")
    assert acct["available"] is None
    assert acct["credit_limit"] is None
