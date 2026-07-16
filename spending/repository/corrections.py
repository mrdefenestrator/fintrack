from sqlalchemy import Connection, insert, select, update

from spending.models import transaction_corrections


def apply_transaction_correction(
    conn: Connection,
    transaction_id: int,
    *,
    category: str | None = None,
    merchant_name: str | None = None,
    notes: str | None = None,
) -> None:
    existing = conn.execute(
        select(transaction_corrections.c.id).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()

    if existing:
        values = {}
        if category is not None:
            values["category"] = category
        if merchant_name is not None:
            values["merchant_name"] = merchant_name
        if notes is not None:
            values["notes"] = notes
        if values:
            conn.execute(
                update(transaction_corrections)
                .where(transaction_corrections.c.transaction_id == transaction_id)
                .values(**values)
            )
    else:
        conn.execute(
            insert(transaction_corrections).values(
                transaction_id=transaction_id,
                category=category,
                merchant_name=merchant_name,
                notes=notes,
            )
        )
    conn.commit()


def get_correction(conn: Connection, transaction_id: int) -> dict | None:
    row = conn.execute(
        select(transaction_corrections).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()
    return dict(row._mapping) if row else None
