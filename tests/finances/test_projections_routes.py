"""Projections grid (issue #52): a grouped sheet like Holdings — one always-open
band per holding group (Holdings' tones), each with its own sortable header
carrying the month-breakdown links, an Unassigned band for budget flows with no
account, and the Liquid / Net worth totals in the shared pinned footer."""

import re
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine

from fintrack.accounts.balance_history import record_balance
from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.db import init_db
from fintrack.projections.timeline import month_key, shift_month
from fintrack.snapshots.repository import create_snapshot

_TODAY = datetime.now().astimezone().date()
THIS_MONTH = (_TODAY.year, _TODAY.month)


@pytest.fixture()
def client():
    from web.app import create_app

    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        checking = add_account(
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

        # Checking has a point 3 months back, so the past months between it
        # and now carry it forward; Visa has no history before this month.
        y, m = shift_month(THIS_MONTH, -3)
        record_balance(
            conn, account_id=checking, balance=Decimal(800), as_of=date(y, m, 15)
        )
        conn.commit()

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


_PAST = month_key(shift_month(THIS_MONTH, -6))
_NEXT = month_key(shift_month(THIS_MONTH, 1))


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?view=change",
        f"?months=24&month={_NEXT}",
        f"?from={_PAST}",
        f"?from={_PAST}&view=change",
        f"?from={month_key(shift_month(THIS_MONTH, -24))}",
    ],
)
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


def _header(body):
    return body.split('<tr class="sheet-group-header')[1].split("</tr>")[0]


def test_past_window_shows_actuals_then_now_then_projection(client):
    body = _get(client, f"?from={_PAST}&months=12")
    header = _header(body)
    keys = re.findall(r'data-sort-key="(c\d+)"', header)
    # Account, six past months, Now, then the current month and five more.
    assert len(keys) == 14
    now_th = header.split('data-sort-key="c7"')[1].split("</th>")[0]
    assert "Now <span" in now_th
    # Only projected months carry the breakdown link.
    assert header.count("data-header-link") == 6
    # Pager label + Now button are live off the current window.
    assert "data-pager-latest" in body
    # Checking carries its earlier point forward (dimmed, with a tooltip);
    # Visa has nothing recorded yet → blank, and the totals say partial.
    assert "data-carried" in body
    assert "Carried forward: last recorded" in body
    assert "data-missing" in body
    assert "Partial: 1 holding with no value recorded yet" in body
    # Future columns are tinted from the Now column's neighbour on.
    assert ":nth-child(n+9)" in body
    assert ":nth-child(8)" in body  # the divider sits on Now


def test_default_window_matches_the_old_grid(client):
    body = _get(client)
    header = _header(body)
    assert header.count("data-header-link") == 12
    # Now is the first data column; nothing is past, so no divider rule.
    assert "Now <span" in header.split('data-sort-key="c1"')[1].split("</th>")[0]
    assert "var(--tl-rule) 2px" not in body
    # The Now button is disabled on the current window.
    assert "data-pager-latest" not in body
    assert "By " in body


def test_window_entirely_in_the_past(client):
    body = _get(client, f"?from={_PAST}&months=6")
    assert "End of " in body
    assert " since)" in body
    assert "data-header-link" not in _header(body)


def test_breakdown_uses_month_keys_and_ignores_past_months(client):
    body = _get(client, f"?month={_NEXT}")
    assert "data-month-breakdown" in body
    body = _get(client, f"?from={_PAST}&month={_PAST}")
    assert "data-month-breakdown" not in body


def test_estimate_lives_in_the_menu(client):
    body = _get(client)
    menu = body.split("data-proj-menu")[1].split("</details>")[0]
    assert "data-estimate-toggle" in menu
    assert "estimate=1" in menu
