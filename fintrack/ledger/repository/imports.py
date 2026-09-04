import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, delete, func, insert, select, text, update
from sqlalchemy.sql.functions import coalesce

from fintrack.core.models import (
    holdings,
    imports,
    merchant_cache,
    transaction_corrections,
    transactions,
)


def compute_file_hash(file_path: str | Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def check_file_hash(conn: Connection, file_hash: str) -> bool:
    row = conn.execute(
        select(imports.c.id).where(
            imports.c.file_hash == file_hash,
            imports.c.status != "rejected",
        )
    ).fetchone()
    return row is not None


def create_import(
    conn: Connection,
    *,
    account_id: int,
    filename: str,
    file_hash: str,
    ledger_balance: Decimal | None = None,
    ledger_balance_date: date | None = None,
    available_balance: Decimal | None = None,
    available_balance_date: date | None = None,
    beginning_balance: Decimal | None = None,
) -> int:
    # The denormalized group copy backs the DB-level importability check
    # (imports may only target cash/credit-card/loan holdings).
    holding_group = conn.execute(
        select(holdings.c.group_key).where(holdings.c.id == account_id)
    ).scalar()
    if holding_group is None:
        raise ValueError(f"Account id {account_id} not found")
    result = conn.execute(
        insert(imports).values(
            account_id=account_id,
            holding_group=holding_group,
            filename=filename,
            file_hash=file_hash,
            ledger_balance=ledger_balance,
            ledger_balance_date=ledger_balance_date,
            available_balance=available_balance,
            available_balance_date=available_balance_date,
            beginning_balance=beginning_balance,
        )
    )
    conn.commit()
    return result.inserted_primary_key[0]


def insert_transactions(
    conn: Connection,
    *,
    import_id: int,
    account_id: int,
    transactions_data: list[dict],
) -> None:
    for txn in transactions_data:
        conn.execute(
            insert(transactions).values(
                import_id=import_id,
                account_id=account_id,
                date=txn["date"],
                amount=txn["amount"],
                raw_description=txn["raw_description"],
                normalized_merchant=txn["normalized_merchant"],
                fingerprint=txn["fingerprint"],
            )
        )
    conn.commit()


def get_existing_fingerprints(conn: Connection, account_id: int) -> set[str]:
    rows = conn.execute(
        select(transactions.c.fingerprint).where(
            transactions.c.account_id == account_id
        )
    ).fetchall()
    return {row[0] for row in rows}


def get_staging_transactions(conn: Connection, import_id: int) -> list[dict]:
    """Get transactions for a staging import, resolving merchant/category without confirmed filter."""
    stmt = (
        select(
            transactions.c.id,
            transactions.c.date,
            transactions.c.amount,
            transactions.c.raw_description,
            transactions.c.normalized_merchant.label("merchant"),
            coalesce(merchant_cache.c.category, "Uncategorized").label("category"),
        )
        .select_from(transactions)
        .outerjoin(
            merchant_cache,
            transactions.c.normalized_merchant == merchant_cache.c.merchant_name,
        )
        .where(transactions.c.import_id == import_id)
        .order_by(transactions.c.date.desc())
    )
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def get_staging_imports(conn: Connection, snapshot_id: int | None = None) -> list[dict]:
    stmt = (
        select(imports, func.count(transactions.c.id).label("txn_count"))
        .select_from(imports.join(holdings, imports.c.account_id == holdings.c.id))
        .outerjoin(transactions, transactions.c.import_id == imports.c.id)
        .where(imports.c.status == "staging")
        .group_by(imports.c.id)
    )
    if snapshot_id is not None:
        stmt = stmt.where(holdings.c.snapshot_id == snapshot_id)
    stmt = stmt.order_by(imports.c.imported_at.desc())
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def confirm_import(conn: Connection, import_id: int) -> None:
    """Confirm a staged import; captured statement balances land in
    balance_history (with an informational reconciliation note) and the
    account re-syncs to its latest point."""
    from fintrack.accounts.balance_history import (
        reconciliation_note,
        record_balance,
    )

    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="confirmed")
    )
    row = (
        conn.execute(select(imports).where(imports.c.id == import_id)).mappings().one()
    )
    if row["ledger_balance"] is None:
        conn.commit()
        return
    as_of = row["ledger_balance_date"] or (
        row["imported_at"].date() if row["imported_at"] else date.today()
    )
    note = reconciliation_note(
        conn,
        account_id=row["account_id"],
        statement_balance=row["ledger_balance"],
        as_of=as_of,
    )
    record_balance(
        conn,
        account_id=row["account_id"],
        balance=row["ledger_balance"],
        as_of=as_of,
        source="statement",
        available=row["available_balance"],
        import_id=import_id,
        note=note,
    )


def _dup_account_filter(snapshot_id: int | None):
    """Return (join, where) fragments scoping a transactions query to a snapshot."""
    if snapshot_id is None:
        return "", ""
    return (
        " JOIN holdings h ON h.id = t.account_id",
        " WHERE h.snapshot_id = :snapshot_id",
    )


def find_duplicate_transactions(
    conn: Connection, snapshot_id: int | None = None
) -> list[dict]:
    """Transactions that share a fingerprint (i.e. an import would have deduped
    them). Each row describes one duplicated fingerprint: how many copies exist
    and a sample of the underlying transaction. Requires fingerprints to be
    current (see migration f1e2d3c4b5a6)."""
    join, where = _dup_account_filter(snapshot_id)
    stmt = text(
        f"""
        SELECT t.fingerprint AS fingerprint,
               COUNT(*) AS copies,
               MIN(t.id) AS sample_id,
               t.account_id AS account_id,
               t.date AS date,
               t.amount AS amount,
               t.normalized_merchant AS merchant
        FROM transactions t{join}{where}
        GROUP BY t.fingerprint
        HAVING COUNT(*) > 1
        ORDER BY t.date DESC, t.fingerprint
        """
    )
    params = {} if snapshot_id is None else {"snapshot_id": snapshot_id}
    rows = conn.execute(stmt, params).mappings().all()
    return [dict(row) for row in rows]


def remove_duplicate_transactions(
    conn: Connection, snapshot_id: int | None = None
) -> int:
    """Collapse each duplicated fingerprint to one row, mirroring import-time
    dedup. Keeps the row carrying a user correction (else the lowest id) and
    deletes corrections on removed rows. Returns the number removed."""
    join, where = _dup_account_filter(snapshot_id)
    params = {} if snapshot_id is None else {"snapshot_id": snapshot_id}
    dup_ids = [
        r[0]
        for r in conn.execute(
            text(
                f"""
                SELECT id FROM (
                    SELECT t.id AS id,
                           ROW_NUMBER() OVER (
                               PARTITION BY t.fingerprint
                               ORDER BY
                                   (SELECT COUNT(*) FROM transaction_corrections c
                                    WHERE c.transaction_id = t.id) DESC,
                                   t.id ASC
                           ) AS rn
                    FROM transactions t{join}{where}
                )
                WHERE rn > 1
                """
            ),
            params,
        ).all()
    ]
    if not dup_ids:
        return 0
    conn.execute(
        delete(transaction_corrections).where(
            transaction_corrections.c.transaction_id.in_(dup_ids)
        )
    )
    conn.execute(delete(transactions).where(transactions.c.id.in_(dup_ids)))
    conn.commit()
    return len(dup_ids)


def reject_import(conn: Connection, import_id: int) -> None:
    conn.execute(delete(transactions).where(transactions.c.import_id == import_id))
    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="rejected")
    )
    conn.commit()
