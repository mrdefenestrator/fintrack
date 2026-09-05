from pathlib import Path

import yaml
from sqlalchemy import Connection, delete, func, insert, select, update

from fintrack.core.models import (
    budget_entries,
    categories,
    merchant_cache,
    transaction_corrections,
)


def seed_categories(conn: Connection, config_path: str | Path) -> None:
    """Populate the (global) categories table from configs/categories.yaml.

    This only runs when the categories table is completely EMPTY — i.e. on a
    genuinely fresh database. It is called unconditionally on every app/CLI
    startup (see web/app.py create_app and fintrack/cli/helpers.py
    CliContext.engine), so it must be a no-op once any category exists.

    In particular, it must NEVER top up individual defaults that are missing
    from an otherwise-populated table: since categories are user-manageable
    (add/rename/delete), a user deleting a default category (e.g. "Other")
    must have that deletion stick across restarts, not have it silently
    resurrected the next time the app starts.
    """
    existing = conn.execute(select(categories.c.id).limit(1)).fetchone()
    if existing is not None:
        return

    with open(config_path) as f:
        config = yaml.safe_load(f)

    for cat in config["categories"]:
        conn.execute(
            insert(categories).values(name=cat["name"], sort_order=cat["sort_order"])
        )
    conn.commit()


def list_categories(conn: Connection) -> list[dict]:
    rows = conn.execute(select(categories).order_by(categories.c.sort_order)).fetchall()
    return [dict(row._mapping) for row in rows]


def get_category_names(conn: Connection) -> list[str]:
    rows = conn.execute(
        select(categories.c.name).order_by(categories.c.sort_order)
    ).fetchall()
    return [row[0] for row in rows]


def _find_by_name_ci(conn: Connection, name: str):
    """Case-insensitive lookup of a category id by name, or None."""
    return conn.execute(
        select(categories.c.id).where(func.lower(categories.c.name) == name.lower())
    ).fetchone()


def add_category(conn: Connection, *, name: str, sort_order: int | None = None) -> int:
    """Add a new category.

    Rejects duplicates case-insensitively. If sort_order is omitted, the
    category is appended after the current highest sort_order.
    """
    name = name.strip()
    if not name:
        raise ValueError("Category name cannot be empty")
    if _find_by_name_ci(conn, name) is not None:
        raise ValueError(f'Category "{name}" already exists')

    if sort_order is None:
        max_order = conn.execute(select(func.max(categories.c.sort_order))).scalar()
        sort_order = (max_order or 0) + 1

    result = conn.execute(insert(categories).values(name=name, sort_order=sort_order))
    conn.commit()
    return result.inserted_primary_key[0]


def _cascade_rename(conn: Connection, old_name: str, new_name: str) -> None:
    """Update every table that references a category BY NAME."""
    conn.execute(
        update(merchant_cache)
        .where(merchant_cache.c.category == old_name)
        .values(category=new_name)
    )
    conn.execute(
        update(transaction_corrections)
        .where(transaction_corrections.c.category == old_name)
        .values(category=new_name)
    )
    conn.execute(
        update(budget_entries)
        .where(budget_entries.c.category == old_name)
        .values(category=new_name)
    )


def rename_category(conn: Connection, old_name: str, new_name: str) -> None:
    """Rename a category, cascading the name change to every table that
    references categories by name: merchant_cache, transaction_corrections,
    and budget_entries. Rejects if a (case-insensitively) different category
    already has the new name.
    """
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Category name cannot be empty")

    row = conn.execute(
        select(categories.c.id).where(categories.c.name == old_name)
    ).fetchone()
    if row is None:
        raise ValueError(f"Category not found: {old_name}")

    if old_name.lower() != new_name.lower():
        dup = _find_by_name_ci(conn, new_name)
        if dup is not None:
            raise ValueError(f'Category "{new_name}" already exists')

    conn.execute(
        update(categories).where(categories.c.id == row[0]).values(name=new_name)
    )
    if old_name != new_name:
        _cascade_rename(conn, old_name, new_name)
    conn.commit()


def edit_category(
    conn: Connection,
    category_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
) -> None:
    """Update a category by id (used by the CLI). Renaming cascades through
    merchant_cache/transaction_corrections/budget_entries just like
    rename_category, so this can never orphan a reference by name.
    """
    values = {}
    old_name = None
    new_name = None
    if name is not None:
        row = conn.execute(
            select(categories.c.name).where(categories.c.id == category_id)
        ).fetchone()
        if row is None:
            raise ValueError(f"Category id {category_id} not found")
        old_name = row[0]
        new_name = name.strip()
        if not new_name:
            raise ValueError("Category name cannot be empty")
        if old_name.lower() != new_name.lower():
            dup = _find_by_name_ci(conn, new_name)
            if dup is not None:
                raise ValueError(f'Category "{new_name}" already exists')
        values["name"] = new_name
    if sort_order is not None:
        values["sort_order"] = sort_order
    if values:
        conn.execute(
            categories.update().where(categories.c.id == category_id).values(**values)
        )
        if old_name is not None and old_name != new_name:
            _cascade_rename(conn, old_name, new_name)
        conn.commit()


def _usage_counts(conn: Connection, name: str) -> dict[str, int]:
    merchant_count = conn.execute(
        select(func.count())
        .select_from(merchant_cache)
        .where(merchant_cache.c.category == name)
    ).scalar()
    correction_count = conn.execute(
        select(func.count())
        .select_from(transaction_corrections)
        .where(transaction_corrections.c.category == name)
    ).scalar()
    budget_count = conn.execute(
        select(func.count())
        .select_from(budget_entries)
        .where(budget_entries.c.category == name)
    ).scalar()
    return {
        "merchants": merchant_count or 0,
        "corrections": correction_count or 0,
        "budget_entries": budget_count or 0,
    }


def _usage_breakdown_message(counts: dict[str, int]) -> str:
    parts = []
    if counts["merchants"]:
        n = counts["merchants"]
        parts.append(f"{n} merchant{'s' if n != 1 else ''}")
    if counts["corrections"]:
        n = counts["corrections"]
        parts.append(f"{n} correction{'s' if n != 1 else ''}")
    if counts["budget_entries"]:
        n = counts["budget_entries"]
        parts.append(f"{n} budget entr{'ies' if n != 1 else 'y'}")
    return f"In use by {', '.join(parts)}."


def delete_category(conn: Connection, *, name: str) -> None:
    """Delete a category, but only if it is not referenced anywhere.

    Raises ValueError with a human-readable usage breakdown (e.g. "In use by
    12 merchants, 3 corrections, 1 budget entry.") if any of merchant_cache,
    transaction_corrections, or budget_entries still reference it by name.
    """
    counts = _usage_counts(conn, name)
    if any(counts.values()):
        raise ValueError(_usage_breakdown_message(counts))

    result = conn.execute(delete(categories).where(categories.c.name == name))
    if result.rowcount == 0:
        raise ValueError(f"Category not found: {name}")
    conn.commit()


def move_category(conn: Connection, name: str, direction: str) -> None:
    """Reorder a category up or down relative to its neighbors by
    sort_order, mirroring fintrack.accounts.repository.move_account."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    rows = conn.execute(
        select(categories.c.id, categories.c.name, categories.c.sort_order).order_by(
            categories.c.sort_order
        )
    ).all()
    names = [r[1] for r in rows]
    if name not in names:
        raise ValueError(f"Category not found: {name}")
    idx = names.index(name)
    if direction == "up" and idx <= 0:
        return
    if direction == "down" and idx >= len(names) - 1:
        return
    swap_idx = idx - 1 if direction == "up" else idx + 1
    id_a, _, order_a = rows[idx]
    id_b, _, order_b = rows[swap_idx]
    conn.execute(
        update(categories).where(categories.c.id == id_a).values(sort_order=order_b)
    )
    conn.execute(
        update(categories).where(categories.c.id == id_b).values(sort_order=order_a)
    )
    conn.commit()
