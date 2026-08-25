"""Low-level CRUD over the holdings supertype + its subtype detail tables.

The domain repositories (fintrack.accounts.repository for cash/credit cards,
fintrack.networth.repository for assets/loans, fintrack.ledger.repository
.accounts for the import flow) all speak dicts to their callers and delegate
the two-level table mechanics here: joined loads, paired inserts, per-group
sort order, and the group-transition discipline (delete old detail -> update
spine -> insert new detail) that the composite foreign keys enforce.

Nothing here commits; callers own transaction boundaries.
"""

from typing import Any

from sqlalchemy import Connection, Table, delete, func, insert, select, update

from fintrack.core.models import (
    asset_details,
    cash_details,
    credit_card_details,
    holdings,
    loan_details,
)

DETAIL_TABLES: dict[str, Table] = {
    "cash": cash_details,
    "credit_card": credit_card_details,
    "loan": loan_details,
    "asset": asset_details,
}

# Spine columns callers may set through insert/update (id, snapshot_id,
# group_key, sort_order, created_at are managed here).
SPINE_FIELDS = ("type", "name", "institution", "as_of_date")


def _detail_value_columns(group: str) -> list:
    detail = DETAIL_TABLES[group]
    return [
        c for c in detail.c if c.name not in ("holding_id", "group_key", "snapshot_id")
    ]


def load_holdings(
    conn: Connection, snapshot_id: int, groups: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Holdings of the given groups, spine + detail columns merged into one
    dict per row, ordered by the given group order then per-group sort_order."""
    rows: list[dict[str, Any]] = []
    for group in groups:
        detail = DETAIL_TABLES[group]
        stmt = (
            select(holdings, *_detail_value_columns(group))
            .select_from(holdings.join(detail, holdings.c.id == detail.c.holding_id))
            .where(
                holdings.c.snapshot_id == snapshot_id,
                holdings.c.group_key == group,
            )
            .order_by(holdings.c.sort_order, holdings.c.id)
        )
        rows.extend(dict(r) for r in conn.execute(stmt).mappings().all())
    return rows


def get_holding(
    conn: Connection, snapshot_id: int, holding_id: int
) -> dict[str, Any] | None:
    """One holding (any group), spine + detail merged, or None."""
    spine = (
        conn.execute(
            select(holdings).where(
                holdings.c.id == holding_id,
                holdings.c.snapshot_id == snapshot_id,
            )
        )
        .mappings()
        .first()
    )
    if spine is None:
        return None
    row = dict(spine)
    detail = DETAIL_TABLES[row["group_key"]]
    detail_row = (
        conn.execute(
            select(*_detail_value_columns(row["group_key"])).where(
                detail.c.holding_id == holding_id
            )
        )
        .mappings()
        .first()
    )
    if detail_row is not None:
        row.update(dict(detail_row))
    return row


def next_sort_order(conn: Connection, snapshot_id: int, group: str) -> int:
    current = conn.execute(
        select(func.max(holdings.c.sort_order)).where(
            holdings.c.snapshot_id == snapshot_id,
            holdings.c.group_key == group,
        )
    ).scalar()
    return (current if current is not None else -1) + 1


def insert_holding(
    conn: Connection,
    snapshot_id: int,
    group: str,
    spine: dict[str, Any],
    detail: dict[str, Any],
    *,
    sort_order: int | None = None,
) -> int:
    """Insert one holding: spine row + its detail row. Returns the new id."""
    if sort_order is None:
        sort_order = next_sort_order(conn, snapshot_id, group)
    holding_id = conn.execute(
        insert(holdings).values(
            snapshot_id=snapshot_id,
            group_key=group,
            sort_order=sort_order,
            **{k: v for k, v in spine.items() if k in SPINE_FIELDS},
        )
    ).inserted_primary_key[0]
    conn.execute(
        insert(DETAIL_TABLES[group]).values(
            holding_id=holding_id,
            group_key=group,
            snapshot_id=snapshot_id,
            **detail,
        )
    )
    return holding_id


def update_holding(
    conn: Connection,
    snapshot_id: int,
    holding_id: int,
    old_group: str,
    new_group: str,
    spine_updates: dict[str, Any],
    detail_values: dict[str, Any],
) -> None:
    """Update a holding's spine and detail row.

    Same group: apply spine updates and set the given detail columns. Group
    change: delete the old detail row, move the spine (fresh sort_order at the
    end of the new group), and insert a new detail row from detail_values —
    the ordering the composite FKs require. A group change is rejected by the
    DB (IntegrityError) when the holding still has import history pinning it
    to an importable group it is leaving for 'asset', or when its old detail
    row is still referenced (e.g. a cash account serving as a payment ref).
    """
    spine = {k: v for k, v in spine_updates.items() if k in SPINE_FIELDS}
    if new_group == old_group:
        if spine:
            conn.execute(
                update(holdings).where(holdings.c.id == holding_id).values(**spine)
            )
        if detail_values:
            detail = DETAIL_TABLES[old_group]
            conn.execute(
                update(detail)
                .where(detail.c.holding_id == holding_id)
                .values(**detail_values)
            )
        return
    old_detail = DETAIL_TABLES[old_group]
    conn.execute(delete(old_detail).where(old_detail.c.holding_id == holding_id))
    conn.execute(
        update(holdings)
        .where(holdings.c.id == holding_id)
        .values(
            group_key=new_group,
            sort_order=next_sort_order(conn, snapshot_id, new_group),
            **spine,
        )
    )
    conn.execute(
        insert(DETAIL_TABLES[new_group]).values(
            holding_id=holding_id,
            group_key=new_group,
            snapshot_id=snapshot_id,
            **detail_values,
        )
    )


def delete_holding(
    conn: Connection, snapshot_id: int, holding_id: int, groups: tuple[str, ...]
) -> int:
    """Delete a holding if it belongs to one of the given groups. Returns the
    number of rows deleted (0 = not found). IntegrityError propagates when the
    holding is still referenced (payment ref, secured-asset ref, budget)."""
    result = conn.execute(
        delete(holdings).where(
            holdings.c.id == holding_id,
            holdings.c.snapshot_id == snapshot_id,
            holdings.c.group_key.in_(groups),
        )
    )
    return result.rowcount


def _ordered_ids_with_groups(
    conn: Connection, snapshot_id: int, groups: tuple[str, ...]
) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for group in groups:
        rows.extend(
            (r[0], group)
            for r in conn.execute(
                select(holdings.c.id)
                .where(
                    holdings.c.snapshot_id == snapshot_id,
                    holdings.c.group_key == group,
                )
                .order_by(holdings.c.sort_order, holdings.c.id)
            ).all()
        )
    return rows


def reorder_merged(
    conn: Connection,
    snapshot_id: int,
    groups: tuple[str, ...],
    new_order: list[int],
) -> None:
    """Persist a permutation of the merged (group-ordered) holdings list.

    `new_order[k]` is the old merged position of the row that should land at
    merged position k — the same contract fintrack.core.ordering uses. Rows
    never actually move between groups (the web UI reorders within one group,
    holding the others fixed), so the permutation is decomposed per group:
    each group's rows are renumbered 0..n-1 in their permuted order.
    """
    rows = _ordered_ids_with_groups(conn, snapshot_id, groups)
    n = len(rows)
    if sorted(new_order) != list(range(n)):
        raise ValueError("new_order must be a permutation of the current positions")
    per_group_pos: dict[str, int] = {g: 0 for g in groups}
    for old_pos in new_order:
        holding_id, group = rows[old_pos]
        conn.execute(
            update(holdings)
            .where(holdings.c.id == holding_id)
            .values(sort_order=per_group_pos[group])
        )
        per_group_pos[group] += 1


def swap_adjacent(
    conn: Connection,
    snapshot_id: int,
    groups: tuple[str, ...],
    holding_id: int,
    direction: str,
) -> None:
    """Move a holding up/down one slot within its own group. Moving past the
    group's edge is a no-op (rows never leave their group by reordering)."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = _ordered_ids_with_groups(conn, snapshot_id, groups)
    ids = [hid for hid, _ in rows]
    if holding_id not in ids:
        raise ValueError(f"Holding id {holding_id} not found")
    idx = ids.index(holding_id)
    group = rows[idx][1]
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(rows) or rows[swap_idx][1] != group:
        return
    group_rows = [i for i, (_, g) in enumerate(rows) if g == group]
    # Renumber the group 0..n-1 with the two rows swapped, healing any legacy
    # duplicate sort_order values along the way.
    order = [rows[i][0] for i in group_rows]
    a, b = order.index(holding_id), order.index(rows[swap_idx][0])
    order[a], order[b] = order[b], order[a]
    for pos, hid in enumerate(order):
        conn.execute(
            update(holdings).where(holdings.c.id == hid).values(sort_order=pos)
        )
