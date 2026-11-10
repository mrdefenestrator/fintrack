"""Spending-alert data (issue #101): the history the checks read, and the
alert_dismissals overlay that remembers what the user dismissed.

The checks themselves are pure functions in fintrack.ledger.alerts; nothing
here (or there) sends transaction data anywhere.
"""

from datetime import date

from sqlalchemy import Connection, delete, select
from sqlalchemy.dialects.sqlite import insert

from fintrack.core.models import alert_dismissals, holdings, transactions
from fintrack.ledger.repository.aggregations import base_transaction_query


def get_alert_history(conn: Connection, snapshot_id: int, until: date) -> list[dict]:
    """Every confirmed transaction in the snapshot up to ``until`` (hidden
    duplicates left out): id, date, amount, merchant, category, import_id."""
    subq = (
        base_transaction_query(snapshot_id)
        .where(transactions.c.date <= until)
        .subquery()
    )
    stmt = select(
        subq.c.id,
        subq.c.date,
        subq.c.amount,
        subq.c.merchant,
        subq.c.category,
        subq.c.import_id,
    ).order_by(subq.c.date, subq.c.id)
    return [dict(r._mapping) for r in conn.execute(stmt)]


def get_dismissals(
    conn: Connection, snapshot_id: int
) -> tuple[set[int], set[tuple[str, date]]]:
    """(dismissed transaction ids, dismissed (category, month-start) pairs)."""
    rows = conn.execute(
        select(
            alert_dismissals.c.transaction_id,
            alert_dismissals.c.category,
            alert_dismissals.c.month,
        ).where(alert_dismissals.c.snapshot_id == snapshot_id)
    )
    txn_ids: set[int] = set()
    categories: set[tuple[str, date]] = set()
    for txn_id, category, month in rows:
        if txn_id is not None:
            txn_ids.add(txn_id)
        else:
            categories.add((category, month))
    return txn_ids, categories


def _check_transaction(conn: Connection, snapshot_id: int, transaction_id: int) -> None:
    owner = conn.execute(
        select(holdings.c.snapshot_id)
        .select_from(
            transactions.join(holdings, transactions.c.account_id == holdings.c.id)
        )
        .where(transactions.c.id == transaction_id)
    ).scalar()
    if owner != snapshot_id:
        raise LookupError(f"No transaction {transaction_id} in this snapshot")


def dismiss_transaction_alert(
    conn: Connection, snapshot_id: int, transaction_id: int
) -> None:
    """Hide every alert on a transaction. Idempotent; LookupError when the
    transaction isn't in the snapshot."""
    _check_transaction(conn, snapshot_id, transaction_id)
    conn.execute(
        insert(alert_dismissals)
        .values(snapshot_id=snapshot_id, transaction_id=transaction_id)
        .on_conflict_do_nothing()
    )
    conn.commit()


def undismiss_transaction_alert(
    conn: Connection, snapshot_id: int, transaction_id: int
) -> bool:
    """Bring a transaction's alerts back; False when none were dismissed."""
    result = conn.execute(
        delete(alert_dismissals).where(
            alert_dismissals.c.snapshot_id == snapshot_id,
            alert_dismissals.c.transaction_id == transaction_id,
        )
    )
    conn.commit()
    return result.rowcount > 0


def dismiss_category_alert(
    conn: Connection, snapshot_id: int, category: str, month: date
) -> None:
    """Hide a category's running-hot alert for one month. Idempotent."""
    conn.execute(
        insert(alert_dismissals)
        .values(snapshot_id=snapshot_id, category=category, month=month.replace(day=1))
        .on_conflict_do_nothing()
    )
    conn.commit()


def undismiss_category_alert(
    conn: Connection, snapshot_id: int, category: str, month: date
) -> bool:
    """Bring a category's running-hot alert back for that month."""
    result = conn.execute(
        delete(alert_dismissals).where(
            alert_dismissals.c.snapshot_id == snapshot_id,
            alert_dismissals.c.category == category,
            alert_dismissals.c.month == month.replace(day=1),
        )
    )
    conn.commit()
    return result.rowcount > 0
