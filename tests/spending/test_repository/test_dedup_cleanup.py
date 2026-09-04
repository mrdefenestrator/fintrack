from datetime import date
from decimal import Decimal

from sqlalchemy import select

from fintrack.core.models import transaction_corrections, transactions
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.corrections import apply_transaction_correction
from fintrack.ledger.repository.imports import (
    create_import,
    find_duplicate_transactions,
    insert_transactions,
    remove_duplicate_transactions,
)


def _txn(fp, desc="COFFEE", amount="-12.50", day=1):
    return {
        "date": date(2026, 1, day),
        "amount": Decimal(amount),
        "raw_description": desc,
        "normalized_merchant": desc.lower(),
        "fingerprint": fp,
    }


def _ids_by_fp(conn, fp):
    return [
        r[0]
        for r in conn.execute(
            select(transactions.c.id)
            .where(transactions.c.fingerprint == fp)
            .order_by(transactions.c.id)
        ).all()
    ]


def _seed(conn):
    acct = add_account(conn, name="Chk", institution="B", account_type="checking")
    imp1 = create_import(conn, account_id=acct, filename="a.ofx", file_hash="h1")
    imp2 = create_import(conn, account_id=acct, filename="b.ofx", file_hash="h2")
    # import 1: a duplicated fingerprint, a legit in-file repeat, and a unique row
    insert_transactions(
        conn,
        import_id=imp1,
        account_id=acct,
        transactions_data=[
            _txn("dup"),
            _txn("rep0", desc="BUS", amount="-5.00", day=2),
            _txn("rep1", desc="BUS", amount="-5.00", day=2),
            _txn("u1", desc="RENT", amount="-900.00", day=3),
        ],
    )
    # import 2: the bug's re-imported copy of the duplicated fingerprint
    insert_transactions(
        conn, import_id=imp2, account_id=acct, transactions_data=[_txn("dup")]
    )
    return acct


def test_find_duplicate_transactions(conn):
    _seed(conn)
    groups = find_duplicate_transactions(conn)
    assert len(groups) == 1
    assert groups[0]["fingerprint"] == "dup"
    assert groups[0]["copies"] == 2


def test_remove_duplicates_keeps_one(conn):
    _seed(conn)
    removed = remove_duplicate_transactions(conn)
    assert removed == 1
    # Exactly one "dup" row remains; the legit repeat and unique row untouched.
    assert len(_ids_by_fp(conn, "dup")) == 1
    assert len(_ids_by_fp(conn, "rep0")) == 1
    assert len(_ids_by_fp(conn, "rep1")) == 1
    assert len(_ids_by_fp(conn, "u1")) == 1
    assert find_duplicate_transactions(conn) == []


def test_remove_keeps_corrected_row(conn):
    _seed(conn)
    dup_ids = _ids_by_fp(conn, "dup")
    corrected = dup_ids[-1]  # the higher id, which the lowest-id rule would drop
    apply_transaction_correction(conn, corrected, category="Coffee")

    removed = remove_duplicate_transactions(conn)
    assert removed == 1
    assert _ids_by_fp(conn, "dup") == [corrected]
    surviving_corrections = conn.execute(
        select(transaction_corrections.c.transaction_id)
    ).all()
    assert surviving_corrections == [(corrected,)]
