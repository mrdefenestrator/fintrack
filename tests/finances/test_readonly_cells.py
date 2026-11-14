"""Issue #147: on an unlocked sheet, every field that can't be edited is greyed
out, the same way on every table.

Renders each sheet that honors edit mode from the dense example seed (it fills
every column: linked transactions, duplicates, alerts, recurring series, live
prices, loans) and checks each data-row cell: it is either click-to-edit, an
open editor, the actions column, or greyed (`cell-readonly`,
partials/sheet.html readonly()). Holdings' compound Details cell is checked
field by field.
"""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from sqlalchemy import create_engine

from fintrack.core.db import init_db
from fintrack.ledger.repository.categories import add_category

_SEED = Path(__file__).resolve().parents[2] / "scripts" / "seed_example.py"


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed_example", _SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    # The seed's live-priced assets would hit the price feeds: keep it offline.
    with (
        patch("fintrack.networth.prices._fetch_prices", return_value={}),
        app.test_client() as c,
    ):
        c.snapshot = seed.DENSE_SNAPSHOT
        yield c


# Data rows of each sheet, by their row ids / markers.
_DATA_ROW = (
    "tr[id^='holding-row-'], tr[id^='budget-row-'], tr[id^='txn-row-'], "
    "tr[id^='merchant-row-'], tr[id^='category-row-'], tr[data-recurring]"
)


def _classes(tag) -> list[str]:
    return tag.get("class") or []


def _editable(tag) -> bool:
    """Click-to-edit (or open editor): the element or a child opens one."""
    if tag.has_attr("hx-get") or "cell-editing" in _classes(tag):
        return True
    return tag.find(attrs={"hx-get": True}) is not None


def _readonly(tag) -> bool:
    return "cell-readonly" in _classes(tag)


def _ungreyed(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(_DATA_ROW)
    assert rows, "no data rows rendered"
    bad = []
    for tr in rows:
        for td in tr.find_all("td", recursive=False):
            if "table-actions-cell" in _classes(td):
                continue
            if "holdings-details" in _classes(td):
                grid = td.find("div")
                for field in grid.find_all(recursive=False):
                    if not (_editable(field) or _readonly(field)):
                        bad.append(f"{tr.get('id')} f-{field.get('data-sort-field')}")
                continue
            if not (_editable(td) or _readonly(td)):
                bad.append(f"{tr.get('id') or tr.get('data-recurring')}: {td}")
    return bad


@pytest.mark.parametrize(
    "path",
    [
        "holdings",
        "budget",
        "budget?group=kind",
        "budget?view=recurring",
        "transactions?all_months=true",
        "merchants",
        "categories",
    ],
)
def test_every_uneditable_cell_is_greyed_in_edit_mode(client, path):
    sep = "&" if "?" in path else "?"
    resp = client.get(f"/s/{client.snapshot}/{path}{sep}edit=1")
    assert resp.status_code == 200
    assert _ungreyed(resp.get_data(as_text=True)) == []


@pytest.mark.parametrize(
    "path", ["holdings", "budget", "transactions?all_months=true", "merchants"]
)
def test_view_mode_greys_nothing(client, path):
    """Locked sheets read normally: the greying is edit mode's cue only."""
    soup = BeautifulSoup(
        client.get(f"/s/{client.snapshot}/{path}").get_data(as_text=True),
        "html.parser",
    )
    rows = soup.select(_DATA_ROW)
    assert rows
    assert not any(r.select(".cell-readonly") for r in rows)


def test_budget_actuals_are_greyed_in_edit_mode(client):
    """Actual / Delta / Status are computed: greyed when unlocked, colored
    when locked."""
    soup = BeautifulSoup(
        client.get(f"/s/{client.snapshot}/budget?edit=1").get_data(as_text=True),
        "html.parser",
    )
    for sel in ("td[data-actual]", "td[data-delta]", "td[data-status]"):
        cells = soup.select(sel)
        assert cells, sel
        assert all(_readonly(td) for td in cells), sel
