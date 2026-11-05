"""The unbanded ("flat") grouped sheets — Transactions, Merchants, Categories,
Budget vs Actual (issue #48) — render on the shared grouped-sheet layer
(partials/grouped_sheet.html) without bands: the column header in a <thead>,
one untoned ``sheet-group--flat`` body, and the pinned footer in its own
trailing tbody. Every row spans the full grid, as on the banded sheets."""

from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine

from fintrack.budget.repository import add_budget_entry
from fintrack.core.db import init_db
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.categories import add_category
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.snapshots.repository import create_snapshot
from web.routes import FLAT_SHEETS

PAGES = {
    "transactions": "/s/home/transactions?year=2024&month=1",
    "merchants": "/s/home/merchants",
    "categories": "/s/home/categories",
    "actual": "/s/home/budget/actual?year=2024&month=1",
}


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        add_category(conn, name="Groceries")
        acct_id = add_account(
            conn,
            name="Chase",
            institution="Chase",
            account_type="credit_card",
            snapshot_id=sid,
        )
        imp_id = create_import(
            conn, account_id=acct_id, filename="t.ofx", file_hash="h1"
        )
        confirm_import(conn, imp_id)
        insert_transactions(
            conn,
            import_id=imp_id,
            account_id=acct_id,
            transactions_data=[
                {
                    "date": date(2024, 1, 15),
                    "amount": Decimal("-42.50"),
                    "raw_description": "WHOLE FOODS #1234",
                    "normalized_merchant": "WHOLE FOODS",
                    "fingerprint": "fp1",
                },
            ],
        )
        set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
        add_budget_entry(
            conn,
            sid,
            {
                "kind": "expense",
                "description": "Food",
                "amount": 400,
                "recurrence": "monthly",
                "category": "Groceries",
            },
        )
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


class _Sheet(HTMLParser):
    """Each <tr>'s summed colspan in the page's grouped-sheet table, plus which
    section (thead / tbody class) it sits in."""

    def __init__(self):
        super().__init__()
        self.depth = 0  # >0 while inside the grouped-sheet table
        self.section = None
        self.rows: list[tuple[str, str, int]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "grouped-sheet" in (a.get("class") or "").split():
            self.depth = 1
        elif not self.depth:
            return
        elif tag == "thead":
            self.section = "thead"
        elif tag == "tbody":
            self.section = a.get("class") or "tbody"
        elif tag == "tr":
            self.rows.append((self.section, a.get("class") or "", 0))
        elif tag in ("td", "th") and self.rows:
            sec, cls, n = self.rows[-1]
            self.rows[-1] = (sec, cls, n + int(a.get("colspan") or 1))

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth = 0


def _parse(client, url):
    resp = client.get(url)
    assert resp.status_code == 200
    parser = _Sheet()
    parser.feed(resp.get_data(as_text=True))
    assert parser.rows
    return parser.rows


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("edit", [False, True])
def test_every_row_spans_the_full_grid(client, page, edit):
    url = PAGES[page] + ("&" if "?" in PAGES[page] else "?") + "edit=1" * edit
    rows = _parse(client, url)
    spans = {n for _, _, n in rows}
    assert len(spans) == 1, rows
    # Only Categories is editable row-by-row with an actions column; it exists
    # only in edit mode, as on Holdings/Budget.
    ncols = FLAT_SHEETS[page]["ncols"]
    assert spans == {ncols + 1 if (edit and page == "categories") else ncols}


@pytest.mark.parametrize("page", PAGES)
def test_flat_sheet_structure(client, page):
    rows = _parse(client, PAGES[page])
    sections = [sec for sec, _, _ in rows]
    # Header in <thead>, then the one flat body, then the pinned footer.
    assert sections[0] == "thead"
    assert "sheet-group-header" in rows[0][1]
    assert any(sec == "sheet-group sheet-group--flat" for sec in sections)
    assert not any("sheet-group-heading" in cls for _, cls, _ in rows)
    totals = [(sec, cls) for sec, cls, _ in rows if "total-row" in cls]
    assert len(totals) == 1
    assert totals[0][0] == "tbody"  # its own trailing tbody, not the data body
    assert sections[-1] == "tbody"


def test_flat_body_has_no_tone(client):
    body = client.get(PAGES["transactions"]).get_data(as_text=True)
    tbody = body.split('id="txn-tbody"')[1].split(">")[0]
    assert "data-tone" not in tbody
    assert 'data-group="transactions"' in tbody
