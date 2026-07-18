from calendar import monthrange
from datetime import date

from sqlalchemy import Connection, func, select

from fintrack.core.coerce import (
    AMOUNT_FILTER_TOLERANCE,
    AmountFilterSpec,
    parse_amount_filter,
)
from fintrack.ledger.repository.aggregations import base_transaction_query


def _apply_amount_filter(stmt, amount_col, spec: AmountFilterSpec):
    """Apply a parsed AmountFilterSpec to a query, comparing abs(amount)."""
    abs_col = func.abs(amount_col)
    if spec.kind == "tolerance":
        low = spec.value - AMOUNT_FILTER_TOLERANCE
        high = spec.value + AMOUNT_FILTER_TOLERANCE
        stmt = stmt.where(abs_col.between(low, high))
        if spec.sign == "neg":
            stmt = stmt.where(amount_col < 0)
        elif spec.sign == "pos":
            stmt = stmt.where(amount_col > 0)
    elif spec.kind == "range":
        stmt = stmt.where(abs_col.between(spec.low, spec.high))
    elif spec.kind == "gt":
        stmt = stmt.where(abs_col > spec.value)
    elif spec.kind == "gte":
        stmt = stmt.where(abs_col >= spec.value)
    elif spec.kind == "lt":
        stmt = stmt.where(abs_col < spec.value)
    elif spec.kind == "lte":
        stmt = stmt.where(abs_col <= spec.value)
    return stmt


def get_transactions(
    conn: Connection,
    *,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    account_id: int | None = None,
    search: str | None = None,
    status: str | None = None,
    amount: str | None = None,
    import_id: int | None = None,
    snapshot_id: int | None = None,
) -> list[dict]:
    """Get transactions with resolved category and merchant.

    Filters are optional and combine with AND. `amount` accepts the search
    syntax parsed by fintrack.core.coerce.parse_amount_filter (e.g.
    "12.34", "-12.34", "10-20", ">50"); invalid amount text is ignored.
    """
    from fintrack.core.models import transactions

    subq = base_transaction_query(snapshot_id)

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

    amount_spec = parse_amount_filter(amount)
    if amount_spec is not None:
        stmt = _apply_amount_filter(stmt, subq.c.amount, amount_spec)

    if status == "corrected":
        stmt = stmt.where(subq.c.correction_id.isnot(None))
    elif status == "uncategorized":
        stmt = stmt.where(subq.c.category == "Uncategorized")
    elif status == "categorized":
        stmt = stmt.where(
            subq.c.category != "Uncategorized",
            subq.c.correction_id.is_(None),
        )

    # Default (newest-first) order; column sorting is done client-side in the
    # web UI (web/static/js/sortable.js).
    stmt = stmt.order_by(subq.c.date.desc())

    rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]
