"""Budget repository: CRUD + move for budget entries within a snapshot.

Budget history (issue #136): an entry is in the plan from ``start_month``
through ``end_month`` (NULL = unbounded), and its amount and schedule
(``VERSIONED_FIELDS``) can change over time. The schedule columns on
``budget_entries`` are the *base* version; each ``budget_entry_versions`` row
is a full set of those fields in effect from its month until the next. Reads
take an ``as_of`` month: an entry's flat fields are the version in effect
then, and ``_versions`` (present only when an entry has dated changes) lets
the recurrence engine resolve any other month. Edits made while viewing a
month split the version covering it (``update_budget_entry``), so earlier
months keep their plan; ``update_budget_version`` corrects one version in
place. What isn't versioned (kind, description, category, auto account, sort
order) is written to the entry and applies to every month.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, update

from fintrack.budget.recurrence import (
    VERSIONED_FIELDS,
    amount_annual,
    as_month,
    entry_for_month,
    version_in_effect,
)
from fintrack.core.coerce import to_date
from fintrack.core.models import (
    budget_entries,
    budget_entry_versions,
    recurring_dismissals,
)
from fintrack.core.ordering import reorder_by_positions
from fintrack.core.types import BudgetEntry
from fintrack.ledger.repository.corrections import purge_link_only_corrections

_ZERO = Decimal(0)

# Versioned dict-API field -> column (same column names on both tables).
_VERSION_COLS = {
    "amount": "amount",
    "recurrence": "recurrence",
    "date": "date",
    "dayOfMonth": "day_of_month",
    "month": "month",
    "dayOfYear": "day_of_year",
    "continuous": "continuous",
}
assert tuple(_VERSION_COLS) == VERSIONED_FIELDS


def this_month(today: date | None = None) -> date:
    """The 1st of the current (local) month."""
    return (today or datetime.now().astimezone().date()).replace(day=1)


def _version_fields(row) -> dict[str, Any]:
    """A row's versioned fields in dict-API form (dates as ISO strings)."""
    r = dict(row)
    out: dict[str, Any] = {}
    for field, col in _VERSION_COLS.items():
        val = r.get(col)
        out[field] = val.isoformat() if col == "date" and val is not None else val
    return out


def _row_to_budget_entry(row, versions: list, as_of: date) -> BudgetEntry:
    r = dict(row)
    entry: BudgetEntry = {
        "kind": r["kind"],
        "description": r["description"],
    }
    if versions:
        entry["_versions"] = [{"from": None, **_version_fields(r)}] + [
            {"from": v["effective_from"].isoformat(), **_version_fields(v)}
            for v in versions
        ]
        fields = version_in_effect(entry, as_of.year, as_of.month)
    else:
        fields = _version_fields(r)
    entry["amount"] = fields["amount"]  # Decimal (Numeric); see calculations._money
    entry["recurrence"] = fields["recurrence"]
    for field in VERSIONED_FIELDS[2:]:
        if fields[field] is not None:
            entry[field] = fields[field]
    if r.get("category") is not None:
        entry["category"] = r["category"]
    if r.get("auto_account_ref") is not None:
        entry["autoAccountRef"] = r["auto_account_ref"]
    if r.get("start_month") is not None:
        entry["startMonth"] = r["start_month"].isoformat()
    if r.get("end_month") is not None:
        entry["endMonth"] = r["end_month"].isoformat()
    # Store DB row id as _db_id for index operations
    entry["_db_id"] = r["id"]
    return entry


def _versions_by_entry(conn: Connection, entry_ids) -> dict[int, list]:
    rows = (
        conn.execute(
            select(budget_entry_versions)
            .where(budget_entry_versions.c.entry_id.in_(entry_ids))
            .order_by(budget_entry_versions.c.effective_from)
        )
        .mappings()
        .all()
    )
    out: dict[int, list] = {}
    for row in rows:
        out.setdefault(row["entry_id"], []).append(row)
    return out


def get_budget_entries(
    conn: Connection, snapshot_id: int, as_of: date | None = None
) -> list[BudgetEntry]:
    """Every entry in the snapshot (whether or not it's in the plan in
    ``as_of``'s month — see ``recurrence.entry_active``), in sort order, with
    its fields as of that month (default: this month)."""
    as_of = this_month(as_of)
    rows = (
        conn.execute(
            select(budget_entries)
            .where(budget_entries.c.snapshot_id == snapshot_id)
            .order_by(budget_entries.c.sort_order)
        )
        .mappings()
        .all()
    )
    versions = _versions_by_entry(
        conn,
        select(budget_entries.c.id).where(budget_entries.c.snapshot_id == snapshot_id),
    )
    return [_row_to_budget_entry(r, versions.get(r["id"], []), as_of) for r in rows]


def get_budget_entry(
    conn: Connection, snapshot_id: int, db_id: int, as_of: date | None = None
) -> BudgetEntry | None:
    """One budget entry by row id, or None when it isn't in ``snapshot_id``."""
    row = (
        conn.execute(
            select(budget_entries).where(
                budget_entries.c.id == db_id,
                budget_entries.c.snapshot_id == snapshot_id,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    versions = _versions_by_entry(conn, [db_id]).get(db_id, [])
    return _row_to_budget_entry(row, versions, this_month(as_of))


def entry_versions(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """An entry's versions, oldest first, each its versioned fields plus
    ``from`` (the month it took effect; the base version's is the entry's
    start month, or None when the entry has no start)."""
    versions = entry.get("_versions") or [
        {"from": None, **{f: entry.get(f) for f in VERSIONED_FIELDS}}
    ]
    out = [dict(v) for v in versions]
    out[0]["from"] = entry.get("startMonth")
    return out


def budgeted_monthly_by_category(
    budget: list[dict],
    months: list[tuple[int, int]] | None = None,
) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Sum of annualized-then-divided-by-12 amounts, keyed by category.

    Returns (totals, kinds) where totals are unsigned monthly sums and kinds
    maps each category to its budget kind ('income' or 'expense'). With
    ``months``, the figure is the average over those months of each month's
    own plan (issue #136): its versions, and only entries in the plan then.
    """
    totals: dict[str, Decimal] = {}
    kinds: dict[str, str] = {}
    for entry in budget:
        cat = entry.get("category")
        kind = entry.get("kind")
        if not cat or kind not in ("income", "expense"):
            continue
        planned = (
            [entry]
            if not months
            else [e for y, m in months if (e := entry_for_month(entry, y, m))]
        )
        if not planned:
            continue
        # Sum first, then divide: a constant entry's average is then exactly
        # its annual ÷ 12.
        annual = sum((amount_annual(e) for e in planned), _ZERO)
        if months:
            annual = annual / Decimal(len(months))
        totals[cat] = totals.get(cat, _ZERO) + annual / Decimal(12)
        kinds[cat] = kind
    return totals, kinds


def _next_sort_order(conn: Connection, snapshot_id: int) -> int:
    row = conn.execute(
        select(func.max(budget_entries.c.sort_order)).where(
            budget_entries.c.snapshot_id == snapshot_id
        )
    ).scalar()
    return (row or 0) + 1


def add_budget_entry(conn: Connection, snapshot_id: int, entry: dict[str, Any]) -> int:
    """Append an entry to the snapshot's budget; returns its row id. Its
    ``startMonth`` / ``endMonth`` (optional) bound the months it's planned."""
    sort_order = _next_sort_order(conn, snapshot_id)
    row = _entry_dict_to_row(entry, snapshot_id, sort_order)
    _check_span(row.get("start_month"), row.get("end_month"))
    db_id = conn.execute(insert(budget_entries).values(**row)).inserted_primary_key[0]
    conn.commit()
    return db_id


def _split_updates(
    updates: dict[str, Any], delete_keys: list[str] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(entry-level column values, versioned column values) for an edit;
    deleted keys become None. Unknown fields are ignored."""
    entry_cols: dict[str, Any] = {}
    version_cols: dict[str, Any] = {}
    changes = dict(updates)
    for k in delete_keys or []:
        changes[k] = None
    for field, value in changes.items():
        col = _field_to_col(field)
        if col is None:
            continue
        if field in _VERSION_COLS:
            version_cols[col] = to_date(value) if col == "date" else value
        else:
            entry_cols[col] = value
    return entry_cols, version_cols


def _entry_row(conn: Connection, db_id: int):
    return (
        conn.execute(select(budget_entries).where(budget_entries.c.id == db_id))
        .mappings()
        .first()
    )


def _version_rows(conn: Connection, db_id: int) -> list:
    return _versions_by_entry(conn, [db_id]).get(db_id, [])


def _cols_of(row) -> dict[str, Any]:
    r = dict(row)
    return {col: r.get(col) for col in _VERSION_COLS.values()}


def _same_version(a: dict[str, Any], b: dict[str, Any]) -> bool:
    for col in _VERSION_COLS.values():
        x, y = a.get(col), b.get(col)
        if col == "continuous":
            x, y = bool(x), bool(y)
        elif col == "amount":
            x = None if x is None else Decimal(str(x))
            y = None if y is None else Decimal(str(y))
        if x != y:
            return False
    return True


def _collapse_versions(conn: Connection, db_id: int) -> None:
    """Drop versions identical to the one before them (an edit changed back),
    so the history lists real changes only."""
    prev = _cols_of(_entry_row(conn, db_id))
    for v in _version_rows(conn, db_id):
        cols = _cols_of(v)
        if _same_version(prev, cols):
            conn.execute(
                delete(budget_entry_versions).where(
                    budget_entry_versions.c.id == v["id"]
                )
            )
        else:
            prev = cols


def _write_versioned(
    conn: Connection, db_id: int, effective: date, changes: dict[str, Any]
) -> None:
    """Apply versioned changes from ``effective``'s month until the entry's
    next change: edit the version that starts that month in place, or split
    the version covering it (insert a new one from ``effective``)."""
    row = _entry_row(conn, db_id)
    covering = None
    for v in _version_rows(conn, db_id):
        if v["effective_from"] <= effective:
            covering = v
    if covering is not None and covering["effective_from"] == effective:
        conn.execute(
            update(budget_entry_versions)
            .where(budget_entry_versions.c.id == covering["id"])
            .values(**changes)
        )
    elif covering is None and (
        row["start_month"] is not None and effective <= row["start_month"]
    ):
        # The base version starts this month (or later): edit it in place.
        conn.execute(
            update(budget_entries).where(budget_entries.c.id == db_id).values(**changes)
        )
    else:
        fields = _cols_of(covering if covering is not None else row)
        fields.update(changes)
        conn.execute(
            insert(budget_entry_versions).values(
                entry_id=db_id, effective_from=effective, **fields
            )
        )
    _collapse_versions(conn, db_id)


def update_budget_entry(
    conn: Connection,
    snapshot_id: int,
    index: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
    *,
    effective: date | None = None,
) -> None:
    """Edit the entry at ``index``. Versioned fields change from
    ``effective``'s month (default: this month) until the entry's next change;
    earlier months keep their plan. Other fields apply to every month."""
    db_id = _index_to_db_id(conn, snapshot_id, index)
    update_budget_entry_by_id(
        conn, snapshot_id, db_id, updates, delete_keys, effective=effective
    )


def update_budget_entry_by_id(
    conn: Connection,
    snapshot_id: int,
    db_id: int,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
    *,
    effective: date | None = None,
) -> None:
    """``update_budget_entry`` for an entry addressed by row id."""
    row = _entry_row(conn, db_id)
    if row is None or row["snapshot_id"] != snapshot_id:
        raise ValueError(f"Budget entry {db_id} is not in snapshot {snapshot_id}")
    entry_cols, version_cols = _split_updates(updates, delete_keys)
    if entry_cols:
        conn.execute(
            update(budget_entries)
            .where(budget_entries.c.id == db_id)
            .values(**entry_cols)
        )
    if version_cols:
        _write_versioned(conn, db_id, this_month(effective), version_cols)
    conn.commit()


def update_budget_version(
    conn: Connection,
    snapshot_id: int,
    index: int,
    version_from: date | None,
    updates: dict[str, Any],
    delete_keys: list[str] | None = None,
) -> None:
    """Correct one version in place: the one that took effect in
    ``version_from``'s month, or the base version when None. Unlike
    ``update_budget_entry`` nothing is split, so the fix covers exactly the
    months that version did. Only versioned fields are accepted."""
    db_id = _index_to_db_id(conn, snapshot_id, index)
    entry_cols, version_cols = _split_updates(updates, delete_keys)
    if entry_cols:
        raise ValueError("only amount and schedule fields are versioned")
    if not version_cols:
        return
    if version_from is None:
        stmt = update(budget_entries).where(budget_entries.c.id == db_id)
    else:
        target = as_month(version_from)
        found = conn.execute(
            select(budget_entry_versions.c.id).where(
                budget_entry_versions.c.entry_id == db_id,
                budget_entry_versions.c.effective_from == target,
            )
        ).scalar()
        if found is None:
            raise ValueError(f"No version from {target:%b %Y}")
        stmt = update(budget_entry_versions).where(budget_entry_versions.c.id == found)
    conn.execute(stmt.values(**version_cols))
    _collapse_versions(conn, db_id)
    conn.commit()


def delete_budget_version(
    conn: Connection, snapshot_id: int, index: int, version_from: date
) -> None:
    """Remove the change that took effect in ``version_from``'s month; the
    version before it then runs on. The base version can't be removed."""
    db_id = _index_to_db_id(conn, snapshot_id, index)
    result = conn.execute(
        delete(budget_entry_versions).where(
            budget_entry_versions.c.entry_id == db_id,
            budget_entry_versions.c.effective_from == as_month(version_from),
        )
    )
    if result.rowcount == 0:
        raise ValueError(f"No version from {as_month(version_from):%b %Y}")
    _collapse_versions(conn, db_id)
    conn.commit()


_UNSET: Any = object()


def _check_span(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("an entry can't end before it starts")


def set_budget_entry_span(
    conn: Connection,
    snapshot_id: int,
    index: int,
    *,
    start: date | None = _UNSET,
    end: date | None = _UNSET,
) -> None:
    """Set the first and/or last month the entry is planned (None clears a
    bound). Moving the start past dated changes folds the one in effect at the
    new start into the base version; ending drops changes after the end."""
    db_id = _index_to_db_id(conn, snapshot_id, index)
    row = _entry_row(conn, db_id)
    new_start = row["start_month"] if start is _UNSET else as_month(start)
    new_end = row["end_month"] if end is _UNSET else as_month(end)
    _check_span(new_start, new_end)
    if new_start is not None:
        superseded = [
            v for v in _version_rows(conn, db_id) if v["effective_from"] <= new_start
        ]
        if superseded:
            conn.execute(
                update(budget_entries)
                .where(budget_entries.c.id == db_id)
                .values(**_cols_of(superseded[-1]))
            )
            conn.execute(
                delete(budget_entry_versions).where(
                    budget_entry_versions.c.id.in_([v["id"] for v in superseded])
                )
            )
    if new_end is not None:
        conn.execute(
            delete(budget_entry_versions).where(
                budget_entry_versions.c.entry_id == db_id,
                budget_entry_versions.c.effective_from > new_end,
            )
        )
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id)
        .values(start_month=new_start, end_month=new_end)
    )
    _collapse_versions(conn, db_id)
    conn.commit()


def end_budget_entry(
    conn: Connection, snapshot_id: int, index: int, last_month: date | None
) -> None:
    """Stop the entry after ``last_month`` (None resumes it, open-ended). The
    entry, its history, and its linked transactions stay; it just isn't in
    the plan for later months."""
    set_budget_entry_span(conn, snapshot_id, index, end=last_month)


def delete_budget_entry(conn: Connection, snapshot_id: int, index: int) -> None:
    db_id = _index_to_db_id(conn, snapshot_id, index)
    # Linked transactions are unlinked by the FK (ON DELETE SET NULL); drop the
    # correction rows that held nothing but that link so none are left empty.
    purge_link_only_corrections(conn, db_id)
    conn.execute(delete(budget_entries).where(budget_entries.c.id == db_id))
    conn.commit()


def move_budget_entry(
    conn: Connection, snapshot_id: int, index: int, direction: str
) -> None:
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = conn.execute(
        select(budget_entries.c.id, budget_entries.c.sort_order)
        .where(budget_entries.c.snapshot_id == snapshot_id)
        .order_by(budget_entries.c.sort_order)
    ).all()
    n = len(rows)
    if index < 0 or index >= n:
        raise ValueError(f"Budget index {index} out of range (0..{n - 1})")
    if direction == "up" and index <= 0:
        return
    if direction == "down" and index >= n - 1:
        return
    swap_idx = index - 1 if direction == "up" else index + 1
    db_id_a, order_a = rows[index]
    db_id_b, order_b = rows[swap_idx]
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id_a)
        .values(sort_order=order_b)
    )
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.id == db_id_b)
        .values(sort_order=order_a)
    )
    conn.commit()


def reorder_budget_entries(
    conn: Connection, snapshot_id: int, new_order: list[int]
) -> None:
    """Persist a drag-reordered budget-entry order (see core.ordering)."""
    reorder_by_positions(conn, budget_entries, snapshot_id, new_order)


def _index_to_db_id(conn: Connection, snapshot_id: int, index: int) -> int:
    rows = conn.execute(
        select(budget_entries.c.id)
        .where(budget_entries.c.snapshot_id == snapshot_id)
        .order_by(budget_entries.c.sort_order)
    ).all()
    if index < 0 or index >= len(rows):
        raise ValueError(f"Budget index {index} out of range (0..{len(rows) - 1})")
    return rows[index][0]


_FIELD_TO_COL = {
    "kind": "kind",
    "description": "description",
    "amount": "amount",
    "recurrence": "recurrence",
    "category": "category",
    "date": "date",
    "dayOfMonth": "day_of_month",
    "month": "month",
    "dayOfYear": "day_of_year",
    "continuous": "continuous",
    "autoAccountRef": "auto_account_ref",
}


def _field_to_col(field: str) -> str | None:
    return _FIELD_TO_COL.get(field)


def _entry_dict_to_row(
    entry: dict[str, Any], snapshot_id: int, sort_order: int
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "kind": entry["kind"],
        "description": entry.get("description", ""),
        "amount": entry.get("amount", 0),
        "recurrence": entry.get("recurrence", "monthly"),
        "sort_order": sort_order,
        "start_month": as_month(entry.get("startMonth")),
        "end_month": as_month(entry.get("endMonth")),
    }
    optional_map = {
        "category": "category",
        "date": "date",
        "dayOfMonth": "day_of_month",
        "month": "month",
        "dayOfYear": "day_of_year",
        "continuous": "continuous",
        "autoAccountRef": "auto_account_ref",
    }
    for field, col in optional_map.items():
        val = entry.get(field)
        if val is not None:
            row[col] = to_date(val) if col == "date" else val
    return row


# ---------------------------------------------------------------------------
# Recurring-charge dismissals (issue #100)
# ---------------------------------------------------------------------------


def add_recurring_dismissal(conn: Connection, snapshot_id: int, merchant: str) -> None:
    """Mark ``merchant`` "not recurring" in the snapshot. Idempotent."""
    exists = conn.execute(
        select(recurring_dismissals.c.id).where(
            recurring_dismissals.c.snapshot_id == snapshot_id,
            recurring_dismissals.c.merchant == merchant,
        )
    ).first()
    if exists is None:
        conn.execute(
            insert(recurring_dismissals).values(
                snapshot_id=snapshot_id, merchant=merchant
            )
        )
    conn.commit()


def remove_recurring_dismissal(
    conn: Connection, snapshot_id: int, merchant: str
) -> bool:
    """Forget a "not recurring" mark; False when there wasn't one."""
    result = conn.execute(
        delete(recurring_dismissals).where(
            recurring_dismissals.c.snapshot_id == snapshot_id,
            recurring_dismissals.c.merchant == merchant,
        )
    )
    conn.commit()
    return result.rowcount > 0


def get_recurring_dismissals(conn: Connection, snapshot_id: int) -> set[str]:
    """Merchants marked "not recurring" in the snapshot."""
    rows = conn.execute(
        select(recurring_dismissals.c.merchant).where(
            recurring_dismissals.c.snapshot_id == snapshot_id
        )
    )
    return {row[0] for row in rows}
