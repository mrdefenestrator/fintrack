"""Possible-duplicate flags and their resolution (issue #82).

transaction_duplicates is an overlay: raw transactions never change. A row
marked 'duplicate' is hidden from every confirmed-transaction read, and every
such read applies not_hidden_duplicate() so the rule lives in one place.
"""

from datetime import date

from sqlalchemy import Connection, exists, insert, select

from fintrack.core.models import transaction_duplicates, transactions

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
