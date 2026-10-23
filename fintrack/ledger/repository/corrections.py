from sqlalchemy import Connection, delete, insert, select, update

from fintrack.core.models import (
    budget_entries,
    link_suggestion_dismissals,
    transaction_corrections,
)

# Overlay columns that carry a user fix. A correction row with all of these
# NULL carries no information and is pruned (see _prune_if_empty), so a row that
# existed only to hold a budget link disappears when the link is cleared.
_OVERLAY_COLUMNS = ("category", "merchant_name", "notes", "budget_entry_ref")


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


def set_budget_link(
    conn: Connection, transaction_id: int, budget_entry_ref: int | None
) -> None:
    """Link (or, with None, unlink) a transaction to a budget entry.

    The link is a field on the corrections overlay, so setting it upserts the
    row and clearing it prunes the row when no other correction remains. This
    keeps raw transactions immutable and the one-row-per-transaction shape
    enforces "a transaction realizes at most one budget entry". Snapshot
    consistency (the entry and the transaction share a snapshot) is validated by
    the caller (fintrack.budget.reconcile.link_transaction).
    """
    existing = conn.execute(
        select(transaction_corrections).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()

    if existing is None:
        if budget_entry_ref is None:
            return
        conn.execute(
            insert(transaction_corrections).values(
                transaction_id=transaction_id,
                budget_entry_ref=budget_entry_ref,
            )
        )
        conn.commit()
        return

    conn.execute(
        update(transaction_corrections)
        .where(transaction_corrections.c.transaction_id == transaction_id)
        .values(budget_entry_ref=budget_entry_ref)
    )
    _prune_if_empty(conn, transaction_id)
    conn.commit()


def purge_link_only_corrections(conn: Connection, budget_entry_ref: int) -> None:
    """Delete correction rows that exist only to link to ``budget_entry_ref``.

    Called before a budget entry is deleted: its FK is ON DELETE SET NULL, which
    would otherwise leave those rows with every overlay column NULL. Rows that
    also carry a real fix (category, merchant name, notes) are kept and simply
    unlinked by the FK. Does not commit — it runs inside the caller's write.
    """
    other_fixes = [
        getattr(transaction_corrections.c, col)
        for col in _OVERLAY_COLUMNS
        if col != "budget_entry_ref"
    ]
    conn.execute(
        delete(transaction_corrections).where(
            transaction_corrections.c.budget_entry_ref == budget_entry_ref,
            *(c.is_(None) for c in other_fixes),
        )
    )


def _prune_if_empty(conn: Connection, transaction_id: int) -> None:
    """Delete the correction row when every overlay column is NULL."""
    row = conn.execute(
        select(transaction_corrections).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()
    if row is None:
        return
    m = row._mapping
    if all(m[col] is None for col in _OVERLAY_COLUMNS):
        conn.execute(
            delete(transaction_corrections).where(
                transaction_corrections.c.transaction_id == transaction_id
            )
        )


def get_correction(conn: Connection, transaction_id: int) -> dict | None:
    row = conn.execute(
        select(transaction_corrections).where(
            transaction_corrections.c.transaction_id == transaction_id
        )
    ).fetchone()
    return dict(row._mapping) if row else None


def add_link_dismissal(
    conn: Connection, transaction_id: int, budget_entry_ref: int
) -> None:
    """Record that the user rejected suggesting ``budget_entry_ref`` for
    ``transaction_id``. Idempotent. Snapshot consistency is validated by the
    caller (fintrack.budget.reconcile.dismiss_suggestion)."""
    exists = conn.execute(
        select(link_suggestion_dismissals.c.id).where(
            link_suggestion_dismissals.c.transaction_id == transaction_id,
            link_suggestion_dismissals.c.budget_entry_ref == budget_entry_ref,
        )
    ).fetchone()
    if exists is None:
        conn.execute(
            insert(link_suggestion_dismissals).values(
                transaction_id=transaction_id, budget_entry_ref=budget_entry_ref
            )
        )
    conn.commit()


def clear_link_dismissals(conn: Connection, transaction_id: int) -> int:
    """Forget every dismissed suggestion for ``transaction_id``; returns how
    many were removed."""
    result = conn.execute(
        delete(link_suggestion_dismissals).where(
            link_suggestion_dismissals.c.transaction_id == transaction_id
        )
    )
    conn.commit()
    return result.rowcount


def get_link_dismissals(conn: Connection, snapshot_id: int) -> set[tuple[int, int]]:
    """Dismissed ``(transaction_id, budget_entry_ref)`` pairs in a snapshot,
    scoped through the budget entry (which carries the snapshot_id)."""
    stmt = (
        select(
            link_suggestion_dismissals.c.transaction_id,
            link_suggestion_dismissals.c.budget_entry_ref,
        )
        .join(
            budget_entries,
            link_suggestion_dismissals.c.budget_entry_ref == budget_entries.c.id,
        )
        .where(budget_entries.c.snapshot_id == snapshot_id)
    )
    return {(row[0], row[1]) for row in conn.execute(stmt)}
