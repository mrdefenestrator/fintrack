"""Possible-duplicate flags and their resolution (issue #82).

transaction_duplicates is an overlay: raw transactions never change. A row
marked 'duplicate' is hidden from every confirmed-transaction read, and every
such read applies not_hidden_duplicate() so the rule lives in one place.
"""

from datetime import UTC, date, datetime

from sqlalchemy import Connection, exists, insert, select, update

from fintrack.core.models import (
    holdings,
    imports,
    transaction_duplicates,
    transactions,
)

# Rows in these states are already accounted for as someone's possible copy, so
# they are never offered as the original for a new match.
_CLAIMED = ("flagged", "duplicate")


def not_hidden_duplicate(txn_id_col=None):
    """WHERE clause: the transaction is not resolved as a duplicate.

    Safe under an outer join: a NULL transaction id has no flag, so it passes.
    """
    col = transactions.c.id if txn_id_col is None else txn_id_col
    return ~exists().where(
        transaction_duplicates.c.transaction_id == col,
        transaction_duplicates.c.status == "duplicate",
    )


def get_near_dup_candidates(
    conn: Connection, account_id: int, start: date, end: date
) -> list[dict]:
    """Existing rows in the account dated start..end (the caller widens its
    batch's range by the match window) that are not themselves flagged or
    resolved as a duplicate."""
    stmt = (
        select(
            transactions.c.id,
            transactions.c.date,
            transactions.c.amount,
            transactions.c.raw_description,
            transactions.c.normalized_merchant,
            transactions.c.fingerprint,
            transactions.c.external_id,
        )
        .where(
            transactions.c.account_id == account_id,
            transactions.c.date >= start,
            transactions.c.date <= end,
            ~exists().where(
                transaction_duplicates.c.transaction_id == transactions.c.id,
                transaction_duplicates.c.status.in_(_CLAIMED),
            ),
        )
        .order_by(transactions.c.date, transactions.c.id)
    )
    return [dict(r._mapping) for r in conn.execute(stmt)]


def add_duplicate_flags(
    conn: Connection, flags: list[tuple[int, int, str]], *, source: str
) -> None:
    """Record (transaction_id, duplicate_of_id, reason) flags."""
    if not flags:
        return
    conn.execute(
        insert(transaction_duplicates),
        [
            {
                "transaction_id": txn_id,
                "duplicate_of_id": dup_of,
                "reason": reason,
                "source": source,
                "status": "flagged",
            }
            for txn_id, dup_of, reason in flags
        ],
    )
    conn.commit()


DUPLICATE_STATUSES = ("flagged", "duplicate", "not_duplicate")


def _detail_query():
    """Each flag with both sides' facts: the copy (t) and its original (o)."""
    t = transactions.alias("t")
    o = transactions.alias("o")
    ti = imports.alias("ti")
    oi = imports.alias("oi")
    td = transaction_duplicates
    stmt = select(
        td.c.transaction_id,
        td.c.duplicate_of_id,
        td.c.status,
        td.c.reason,
        td.c.source,
        td.c.resolved_at,
        t.c.date,
        t.c.amount,
        t.c.raw_description,
        t.c.import_id,
        ti.c.filename,
        ti.c.status.label("import_status"),
        holdings.c.name.label("account_name"),
        holdings.c.snapshot_id,
        o.c.date.label("of_date"),
        o.c.amount.label("of_amount"),
        o.c.raw_description.label("of_raw_description"),
        o.c.import_id.label("of_import_id"),
        oi.c.filename.label("of_filename"),
        oi.c.status.label("of_import_status"),
    ).select_from(
        td.join(t, td.c.transaction_id == t.c.id)
        .join(ti, t.c.import_id == ti.c.id)
        .join(holdings, t.c.account_id == holdings.c.id)
        .join(o, td.c.duplicate_of_id == o.c.id)
        .join(oi, o.c.import_id == oi.c.id)
    )
    return stmt, t


def list_duplicates(
    conn: Connection,
    snapshot_id: int | None = None,
    *,
    statuses: tuple[str, ...] = ("flagged",),
    import_id: int | None = None,
) -> list[dict]:
    """Flags (with both sides' details), newest copy first."""
    stmt, t = _detail_query()
    stmt = stmt.where(transaction_duplicates.c.status.in_(statuses))
    if snapshot_id is not None:
        stmt = stmt.where(holdings.c.snapshot_id == snapshot_id)
    if import_id is not None:
        stmt = stmt.where(t.c.import_id == import_id)
    stmt = stmt.order_by(t.c.date.desc(), transaction_duplicates.c.id.desc())
    return [dict(r._mapping) for r in conn.execute(stmt)]


def get_duplicate(conn: Connection, transaction_id: int) -> dict | None:
    stmt, _ = _detail_query()
    row = conn.execute(
        stmt.where(transaction_duplicates.c.transaction_id == transaction_id)
    ).first()
    return dict(row._mapping) if row else None


def resolve_duplicate(
    conn: Connection,
    transaction_id: int,
    status: str,
    *,
    snapshot_id: int | None = None,
) -> None:
    """Set a flag's status: 'duplicate' hides the row, 'not_duplicate' keeps
    it, 'flagged' undoes either. Scoped to snapshot_id when given, so a route
    can't touch another household's rows. Raises LookupError when there is no
    such flag."""
    if status not in DUPLICATE_STATUSES:
        raise ValueError(f"Invalid duplicate status: {status}")
    flag = get_duplicate(conn, transaction_id)
    if flag is None or (snapshot_id is not None and flag["snapshot_id"] != snapshot_id):
        raise LookupError(f"Transaction {transaction_id} has no duplicate flag")
    conn.execute(
        update(transaction_duplicates)
        .where(transaction_duplicates.c.transaction_id == transaction_id)
        .values(
            status=status,
            resolved_at=None if status == "flagged" else datetime.now(UTC),
        )
    )
    conn.commit()
