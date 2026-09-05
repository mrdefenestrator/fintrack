"""Shared plumbing for the unified fintrack CLI."""

import sys
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

import click
from tabulate import tabulate

from fintrack.core.config import CATEGORIES_CONFIG
from fintrack.core.db import get_engine, init_db
from fintrack.ledger.repository.categories import seed_categories
from fintrack.snapshots.repository import get_snapshot_id, list_snapshots


class CliContext:
    """Holds the engine and resolves the active snapshot for scoped commands."""

    def __init__(self, db_path: str, snapshot_name: str | None):
        self.db_path = db_path
        self.snapshot_name = snapshot_name
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = get_engine(self.db_path)
            init_db(self._engine)
            with self._engine.connect() as conn:
                seed_categories(conn, CATEGORIES_CONFIG)
        return self._engine

    @contextmanager
    def connect(self):
        with self.engine.connect() as conn:
            yield conn

    def snapshot_id(self, conn) -> int:
        """Resolve --snapshot, defaulting to the sole snapshot in the database."""
        if self.snapshot_name:
            snapshot_id = get_snapshot_id(conn, self.snapshot_name)
            if snapshot_id is None:
                raise click.ClickException(
                    f"Snapshot not found: {self.snapshot_name} "
                    f"(available: {', '.join(list_snapshots(conn)) or 'none'})"
                )
            return snapshot_id
        names = list_snapshots(conn)
        if len(names) == 1:
            return get_snapshot_id(conn, names[0])
        if not names:
            raise click.ClickException(
                "No snapshots exist; create one with `fintrack snapshots add <name>`"
            )
        raise click.ClickException(
            f"Multiple snapshots exist; pass --snapshot ({', '.join(names)})"
        )


pass_cli = click.make_pass_decorator(CliContext)


def sort_items(
    items: list[dict[str, Any]], sort_key: str, reverse: bool = False
) -> list[dict[str, Any]]:
    """Sort dicts by key; None values last, numbers and strings each coherent."""

    def get_sort_value(item):
        val = item.get(sort_key)
        if val is None:
            return (1, "")
        if isinstance(val, (int, float, Decimal)):
            return (0, val)
        return (0, str(val).lower())

    try:
        return sorted(items, key=get_sort_value, reverse=reverse)
    except (KeyError, TypeError):
        print(
            f"Warning: Unable to sort by '{sort_key}', displaying unsorted",
            file=sys.stderr,
        )
        return items


def drop_separator_rows(rows: list[list[Any]]) -> list[list[Any]]:
    return [
        r
        for r in rows
        if not all(isinstance(c, str) and set(c.strip()) <= {"-"} for c in r)
    ]


def echo_table(rows, headers, colalign, show_id: bool = False) -> None:
    if show_id:
        colalign = ("right",) + tuple(colalign)
    click.echo(tabulate(rows, headers=headers, tablefmt="simple", colalign=colalign))


def sort_options(f):
    """--sort / --sort-dir / --show-id, shared by the list commands."""
    f = click.option("--show-id", is_flag=True, help="Show row ids/indexes")(f)
    f = click.option("--sort-dir", type=click.Choice(["asc", "desc"]), default="asc")(f)
    f = click.option("--sort", "sort_key", default=None, help="Field to sort by")(f)
    return f
