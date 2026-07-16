from pathlib import Path

import yaml
from sqlalchemy import Connection, delete, insert, select

from spending.models import categories


def seed_categories(conn: Connection, config_path: str | Path) -> None:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    for cat in config["categories"]:
        existing = conn.execute(
            select(categories).where(categories.c.name == cat["name"])
        ).fetchone()
        if not existing:
            conn.execute(
                insert(categories).values(
                    name=cat["name"], sort_order=cat["sort_order"]
                )
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


def add_category(conn: Connection, *, name: str, sort_order: int) -> None:
    conn.execute(insert(categories).values(name=name, sort_order=sort_order))
    conn.commit()


def edit_category(
    conn: Connection,
    category_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
) -> None:
    values = {}
    if name is not None:
        values["name"] = name
    if sort_order is not None:
        values["sort_order"] = sort_order
    if values:
        conn.execute(
            categories.update().where(categories.c.id == category_id).values(**values)
        )
        conn.commit()


def delete_category(conn: Connection, *, name: str) -> None:
    conn.execute(delete(categories).where(categories.c.name == name))
    conn.commit()
