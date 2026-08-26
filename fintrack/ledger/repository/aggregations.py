from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, extract, func, select
from sqlalchemy.sql.functions import coalesce

from fintrack.core.models import (
    holdings,
    imports,
    merchant_cache,
    transaction_corrections,
    transactions,
)


def _resolved_category():
    return coalesce(
        transaction_corrections.c.category,
        merchant_cache.c.category,
        "Uncategorized",
    ).label("category")


def _resolved_merchant():
    return coalesce(
        transaction_corrections.c.merchant_name,
        transactions.c.normalized_merchant,
    ).label("merchant")


def base_transaction_query(snapshot_id: int | None = None):
    """Base query joining transactions with corrections and merchant cache.

    Only includes confirmed imports; scoped to one snapshot when snapshot_id
    is given.
    """
    stmt = (
        select(
            transactions.c.id,
            transactions.c.date,
            transactions.c.amount,
            transactions.c.raw_description,
            transactions.c.account_id,
            transactions.c.import_id,
            _resolved_merchant(),
            _resolved_category(),
            transaction_corrections.c.id.label("correction_id"),
            transaction_corrections.c.notes.label("notes"),
            holdings.c.name.label("account_name"),
        )
        .select_from(
            transactions.join(imports, transactions.c.import_id == imports.c.id)
        )
        .outerjoin(
            transaction_corrections,
            transactions.c.id == transaction_corrections.c.transaction_id,
        )
        .outerjoin(
            merchant_cache,
            coalesce(
                transaction_corrections.c.merchant_name,
                transactions.c.normalized_merchant,
            )
            == merchant_cache.c.merchant_name,
        )
        .outerjoin(holdings, transactions.c.account_id == holdings.c.id)
        .where(imports.c.status == "confirmed")
    )
    if snapshot_id is not None:
        stmt = stmt.where(holdings.c.snapshot_id == snapshot_id)
    return stmt


def get_monthly_category_totals(
    conn: Connection, *, year: int, month: int, snapshot_id: int | None = None
) -> list[dict]:
    start = date(year, month, 1)
    _, last_day = monthrange(year, month)
    end = date(year, month, last_day)

    subq = (
        base_transaction_query(snapshot_id)
        .where(
            transactions.c.date >= start,
            transactions.c.date <= end,
        )
        .subquery()
    )

    stmt = (
        select(
            subq.c.category,
            func.count().label("count"),
            func.sum(subq.c.amount).label("total"),
        )
        .group_by(subq.c.category)
        .order_by(func.sum(subq.c.amount))
    )

    rows = conn.execute(stmt).fetchall()
    return [{"category": row[0], "count": row[1], "total": row[2]} for row in rows]


def get_monthly_totals_range(
    conn: Connection,
    *,
    start_date: date,
    end_date: date,
    snapshot_id: int | None = None,
) -> list[dict]:
    """Get category totals per month for a date range."""
    subq = (
        base_transaction_query(snapshot_id)
        .where(
            transactions.c.date >= start_date,
            transactions.c.date <= end_date,
        )
        .subquery()
    )

    stmt = (
        select(
            extract("year", subq.c.date).label("year"),
            extract("month", subq.c.date).label("month"),
            subq.c.category,
            func.sum(subq.c.amount).label("total"),
        )
        .group_by("year", "month", subq.c.category)
        .order_by("year", "month")
    )

    rows = conn.execute(stmt).fetchall()
    return [
        {
            "year": int(row[0]),
            "month": int(row[1]),
            "category": row[2],
            "total": row[3],
        }
        for row in rows
    ]


def get_rolling_average(
    conn: Connection,
    *,
    year: int,
    month: int,
    months_back: int = 3,
    snapshot_id: int | None = None,
) -> dict[str, Decimal]:
    """Get trailing N-month average per category."""
    start_month = month - months_back
    start_year = year
    while start_month <= 0:
        start_month += 12
        start_year -= 1

    start = date(start_year, start_month, 1)

    subq = (
        base_transaction_query(snapshot_id)
        .where(
            transactions.c.date >= start,
            transactions.c.date < date(year, month, 1),
        )
        .subquery()
    )

    stmt = select(
        subq.c.category,
        (func.sum(subq.c.amount) / months_back).label("avg"),
    ).group_by(subq.c.category)

    rows = conn.execute(stmt).fetchall()
    return {row[0]: row[1] for row in rows}
