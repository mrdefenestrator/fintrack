import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, delete, func, insert, select, update
from sqlalchemy.sql.functions import coalesce

from spending.models import imports, merchant_cache, transactions


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
    result = conn.execute(
        insert(imports).values(
            account_id=account_id,
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


def get_staging_imports(conn: Connection) -> list[dict]:
    stmt = (
        select(imports, func.count(transactions.c.id).label("txn_count"))
        .select_from(imports)
        .outerjoin(transactions, transactions.c.import_id == imports.c.id)
        .where(imports.c.status == "staging")
        .group_by(imports.c.id)
        .order_by(imports.c.imported_at.desc())
    )
    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def confirm_import(conn: Connection, import_id: int) -> None:
    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="confirmed")
    )
    conn.commit()


def reject_import(conn: Connection, import_id: int) -> None:
    conn.execute(delete(transactions).where(transactions.c.import_id == import_id))
    conn.execute(
        update(imports).where(imports.c.id == import_id).values(status="rejected")
    )
    conn.commit()
