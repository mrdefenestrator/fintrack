"""Issue #148: every sheet's row actions are built on one framework
(partials/row_actions.html) — the same two-slot [ grip ][ ⋯ ] cell on every
data row, a labeled ⋯ menu, and a destructive item last.

Renders each sheet with an actions column from the dense example seed and
checks every data row's cell.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import create_engine

from fintrack.core.db import init_db
from fintrack.ledger.repository.categories import add_category
from tests.finances.test_readonly_cells import _load_seed

_TEMPLATES = Path(__file__).resolve().parents[2] / "web" / "templates"


@pytest.fixture(scope="module")
def client():
    from web.app import create_app

    seed = _load_seed()
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        seed.seed_dense(conn)
        add_category(conn, name="Groceries")  # categories are global, unseeded
    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = engine
    with (
        patch("fintrack.networth.prices._fetch_prices", return_value={}),
        app.test_client() as c,
    ):
        c.snapshot = seed.DENSE_SNAPSHOT
        yield c


_DATA_ROW = (
    "tr[id^='holding-row-'], tr[id^='budget-row-'], tr[id^='category-row-'], "
    "tr[data-recurring]"
)

# (path, whether its rows reorder)
_SHEETS = [
    ("holdings", True),
    ("budget", True),
    ("budget?group=none", True),
    ("budget?group=kind", False),  # Kind bands don't reorder
    ("budget?view=recurring", False),
    ("categories", False),
]


def _page(client, path):
    sep = "&" if "?" in path else "?"
    resp = client.get(f"/s/{client.snapshot}/{path}{sep}edit=1")
    assert resp.status_code == 200
    return BeautifulSoup(resp.get_data(as_text=True), "html.parser")


@pytest.mark.parametrize(("path", "reorders"), _SHEETS)
def test_every_row_has_the_two_slot_cell(client, path, reorders):
    soup = _page(client, path)
    rows = soup.select(_DATA_ROW)
    assert rows, path
    ids = []
    for tr in rows:
        cell = tr.find_all("td", recursive=False)[-1]
        assert "row-actions-cell" in cell["class"], tr.get("id")
        slots = cell.select(".table-actions-inner > .table-action-btn")
        assert len(slots) == 2, tr.get("id")
        grip, menu_btn = slots
        assert ("drag-handle" in grip["class"]) is reorders
        assert "row-menu-btn" in menu_btn["class"]
        menu = cell.select_one(".row-menu[popover]")
        assert menu["id"] == menu_btn["popovertarget"]
        assert menu["role"] == "menu"
        ids.append(menu["id"])
        items = menu.select("[role='menuitem']")
        assert items and all(i.select_one(".row-menu-label").text for i in items)
        # A destructive item, if any, is the last one.
        danger = [i for i in items if "row-menu-item--danger" in i["class"]]
        assert not danger or danger == items[-1:]
    assert len(ids) == len(set(ids)), "menu ids must be unique on the page"


def test_view_mode_has_no_actions(client):
    for path, _ in _SHEETS:
        sep = "&" if "?" in path else "?"
        soup = BeautifulSoup(
            client.get(f"/s/{client.snapshot}/{path}{sep}edit=0").get_data(
                as_text=True
            ),
            "html.parser",
        )
        assert not soup.select(".table-actions-cell, .row-menu, .drag-handle"), path


def test_buttons_come_only_from_the_framework():
    """No template hand-rolls an actions-column button: they all come from
    partials/row_actions.html."""
    offenders = [
        p.relative_to(_TEMPLATES).as_posix()
        for p in _TEMPLATES.rglob("*.html")
        if p.name != "row_actions.html"
        and re.search(
            r'class="[^"]*\b(table-action-btn|drag-handle|row-menu[\w-]*)\b',
            p.read_text(),
        )
    ]
    assert offenders == []
