"""Shared helper for persisting a drag-reordered row order (QA item 3).

The editable Finances sheets (accounts, budget, assets) let the user drag rows
to reorder them. The client posts the new order as a permutation of the rows'
*current* 0-based positions (their `sort_order` rank); this rewrites `sort_order`
to match. Position-based (rather than id-based) keeps it uniform across the three
tables — budget/assets identify rows by position, not primary key.
"""

from sqlalchemy import Table, select, update
from sqlalchemy.engine import Connection


def reorder_by_positions(
    conn: Connection, table: Table, snapshot_id: int, new_order: list[int]
) -> None:
    """Rewrite `sort_order` for a snapshot's rows to the given permutation.

    `new_order[k]` is the *old* position (0-based, by current `sort_order`) of
    the row that should end up at position `k`. `new_order` must be an exact
    permutation of ``range(n)`` for the snapshot's ``n`` rows; anything else
    raises ValueError so a stale/garbled payload never partially reorders.
    """
    ids = [
        r[0]
        for r in conn.execute(
            select(table.c.id)
            .where(table.c.snapshot_id == snapshot_id)
            .order_by(table.c.sort_order)
        ).all()
    ]
    n = len(ids)
    if sorted(new_order) != list(range(n)):
        raise ValueError("new_order must be a permutation of the current positions")
    for new_pos, old_pos in enumerate(new_order):
        conn.execute(
            update(table)
            .where(table.c.id == ids[old_pos], table.c.snapshot_id == snapshot_id)
            .values(sort_order=new_pos)
        )
    conn.commit()
