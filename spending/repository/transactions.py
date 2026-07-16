from calendar import monthrange
from datetime import date

from sqlalchemy import Connection, func, select

from spending.repository.aggregations import base_transaction_query


def get_transactions(
    conn: Connection,
    *,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    status: str | None = None,
    import_id: int | None = None,
    sort: str | None = None,
    sort_dir: str | None = None,
) -> list[dict]:
    """Get transactions with resolved category and merchant.

    Filters are optional and combine with AND.
    """
    from spending.models import transactions

    subq = base_transaction_query()

    if year and month:
        start = date(year, month, 1)
        _, last_day = monthrange(year, month)
        end = date(year, month, last_day)
        subq = subq.where(
            transactions.c.date >= start,
            transactions.c.date <= end,
        )

    if account_id:
        subq = subq.where(transactions.c.account_id == account_id)

    if import_id:
        subq = subq.where(transactions.c.import_id == import_id)

    subq = subq.subquery()

    # Wrap in outer query to filter on resolved columns
    stmt = select(subq)

    if category:
        stmt = stmt.where(subq.c.category == category)

    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            subq.c.raw_description.ilike(pattern) | subq.c.merchant.ilike(pattern)
        )

    if status == "corrected":
        stmt = stmt.where(subq.c.correction_id.isnot(None))
    elif status == "uncategorized":
        stmt = stmt.where(subq.c.category == "Uncategorized")
    elif status == "categorized":
        stmt = stmt.where(
            subq.c.category != "Uncategorized",
            subq.c.correction_id.is_(None),
        )

    _sort_cols = {
        "merchant": subq.c.merchant,
        "description": subq.c.raw_description,
        "account": subq.c.account_name,
        "category": subq.c.category,
        "amount": func.abs(subq.c.amount),
    }
    sort_col = _sort_cols.get(sort) if sort else None
    if sort_col is not None:
        order = sort_col.desc() if sort_dir == "desc" else sort_col.asc()
        stmt = stmt.order_by(order, subq.c.date.desc())
    else:
        stmt = stmt.order_by(subq.c.date.desc())

    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]
