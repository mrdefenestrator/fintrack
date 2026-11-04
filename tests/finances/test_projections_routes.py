"""Projections grid (issue #52): a grouped sheet like Holdings — one always-open
band per holding group (Holdings' tones), each with its own sortable header
carrying the month-breakdown links, an Unassigned band for budget flows with no
account, and the Liquid / Net worth totals in the shared pinned footer."""

import re
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.db import init_db
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def client():
    from web.app import create_app

    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        add_account(
            conn,
            sid,
            {
                "name": "Checking",
                "type": "checking",
                "institution": "Chase",
                "balance": 1000,
            },
        )
        add_account(
            conn,
            sid,
            {
                "name": "Visa",
                "type": "credit_card",
                "institution": "Chase",
                "limit": 1000,
                "balance": -400,
            },
        )
        # No account: lands in the Unassigned band.
        add_budget_entry(
            conn,
            sid,
            {
                "kind": "expense",
                "description": "Gym",
                "amount": 50,
                "recurrence": "monthly",
                "dayOfMonth": 5,
            },
        )

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = engine
    with app.test_client() as c:
        yield c


class _RowSpans(HTMLParser):
    """Sum each <tr>'s cell colspans inside #projections-table."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.rows: list[int] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") == "projections-table":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.rows.append(0)
        elif self.in_table and tag in ("td", "th") and self.rows:
            self.rows[-1] += int(a.get("colspan") or 1)

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False


def _get(client, query=""):
    return client.get(f"/s/home/projections{query}").get_data(as_text=True)


def test_bands_use_the_holdings_groups_and_tones(client):
    body = _get(client)
    bands = re.findall(
        r'<tbody class="sheet-group" data-group="(\w+)" data-tone="(\w+)"', body
    )
    assert bands == [("cash", "emerald"), ("credit", "rose"), ("unassigned", "slate")]
    assert "grouped-sheet" in body
    # No frozen Account column: it ate too much of a narrow viewport.
    assert "sheet-freeze-col" not in body
    # Bands are always open: no collapse toggle.
    assert "x-show" not in body.split('id="projections-table"')[1]


@pytest.mark.parametrize("query", ["", "?view=change", "?months=24&month=2"])
def test_every_row_spans_the_full_grid(client, query):
    parser = _RowSpans()
    parser.feed(_get(client, query))
    assert parser.rows
    assert len(set(parser.rows)) == 1, parser.rows


def test_each_band_header_carries_the_month_links(client):
    body = _get(client, "?months=6")
    headers = body.split('<tr class="sheet-group-header')[1:]
    assert len(headers) == 3
    for h in headers:
        row = h.split("</tr>")[0]
        assert row.count("data-header-link") == 6
        assert "data-sort-key" in row


def test_totals_live_in_the_pinned_footer(client):
    body = _get(client)
    totals = re.findall(r'<tr class="total-row[^"]*"', body)
    assert len(totals) == 2
    assert "Liquid total" in body and "Net worth" in body
    assert "(no account)" in body
    changes = _get(client, "?view=change")
    assert "Liquid change" in changes and "Net worth change" in changes
