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


def test_view_controls_lead_the_filter_bar(client):
    # Window size and Balances / Changes lead the grid's filter bar; Estimate
    # opens its panel (with the filters, #144). The pager sits on its own row
    # above it.
    body = _get(client)
    assert body.index("data-pager-row") < body.index("data-filter-bar")
    lead = body.split("data-bar-lead")[1].split("data-filter-toggle")[0]
    for hook in ("data-window-size", "data-view-toggle"):
        assert hook in lead
    assert "data-pager" not in lead
    panel = body.split("data-filter-panel")[1].split("data-sheet-scroll")[0]
    assert "data-estimate-toggle" in panel
    assert "estimate=1" in panel
    # Compact bar: Reset sits in the panel, and the phone trigger is just its
    # icon (label for screen readers).
    assert "data-filter-reset" in panel
    toggle = body.split("data-filter-toggle")[1].split("</button>")[0]
    assert '<span class="sr-only">Filters</span>' in toggle


# --- Filters and Group by (#144) ---------------------------------------------


@pytest.fixture()
def wide_client():
    """Every holding group, two institutions plus none, and an Unassigned flow."""
    from fintrack.networth.repository import add_asset_entry
    from web.app import create_app

    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        for acct in (
            {"name": "Checking", "type": "checking", "institution": "Chase"},
            {"name": "Savings", "type": "savings", "institution": "Ally"},
            {
                "name": "Visa",
                "type": "credit_card",
                "institution": "Chase",
                "limit": 1000,
                "balance": -400,
            },
        ):
            add_account(conn, sid, {"balance": 1000, **acct})
        add_asset_entry(
            conn,
            sid,
            {
                "kind": "asset",
                "type": "brokerage",
                "name": "Index Fund",
                "institution": "Ally",
                "value": 5000,
            },
        )
        add_asset_entry(
            conn,
            sid,
            {"kind": "debt", "type": "loan", "name": "Car Loan", "balance": 8000},
        )
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
        conn.commit()

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = engine
    with app.test_client() as c:
        yield c


def _bands(body):
    return re.findall(r'<tbody class="sheet-group" data-group="([\w-]+)"', body)


def _names(body):
    """Holding names in the grid's data rows, in order."""
    table = body.split('id="projections-table"')[1].split("total-row")[0]
    return re.findall(
        r'<tr class="sheet-group-row[^"]*"[^>]*>\s*<td[^>]*>([^<]+)</td>', table
    )


def test_filter_bar_offers_group_by_and_holding_filters(wide_client):
    body = _get(wide_client)
    bar = body.split("data-filter-bar")[1].split("data-sheet-scroll")[0]
    assert "data-group-by" in bar
    assert "Holding group" in bar
    for name in ('name="type"', 'name="balance"', 'name="institution"'):
        assert name in bar
    # The view toggles still lead the bar.
    assert "data-bar-lead" in bar and "data-window-size" in bar
    assert bar.index("data-bar-lead") < bar.index("data-group-by")
    # Only the types and institutions present are offered.
    assert 'value="brokerage"' in bar and 'value="vehicle"' not in bar
    assert 'value="Ally"' in bar and 'value="Chase"' in bar


def test_default_grouping_is_the_holdings_bands(wide_client):
    assert _bands(_get(wide_client)) == [
        "cash",
        "credit",
        "loan",
        "asset",
        "unassigned",
    ]


@pytest.mark.parametrize(
    ("query", "names"),
    [
        ("?type=savings&type=brokerage", ["Ally Savings", "Ally Index Fund"]),
        ("?balance=liability", ["Chase Visa", "Car Loan"]),
        ("?institution=Chase", ["Chase Checking", "Chase Visa"]),
        ("?institution=Ally&balance=asset", ["Ally Savings", "Ally Index Fund"]),
    ],
)
def test_filters_narrow_the_rows_and_hide_unassigned(wide_client, query, names):
    body = _get(wide_client, query)
    assert sorted(_names(body)) == sorted(names)
    assert "unassigned" not in _bands(body)
    assert "(no account)" not in body
    # The footer stays snapshot-wide.
    assert "Liquid total" in body and "Net worth" in body
    # Reset clears the filters.
    assert "Reset" in body


def test_unknown_filter_values_are_ignored(wide_client):
    body = _get(wide_client, "?type=vehicle&institution=Nowhere&balance=x")
    assert _bands(body) == ["cash", "credit", "loan", "asset", "unassigned"]


def test_band_subtotals_follow_the_filter(wide_client):
    body = _get(wide_client, "?type=checking")
    heading = body.split('<tr class="sheet-group-heading">')[1].split("</tr>")[0]
    assert "1,000" in heading
    assert "2,000" not in heading


def test_filters_that_match_nothing_show_a_message(wide_client):
    body = _get(wide_client, "?type=checking&balance=liability")
    assert _bands(body) == []
    assert "No holdings match these filters." in body
    parser = _RowSpans()
    parser.feed(body)
    assert len(set(parser.rows)) == 1, parser.rows


@pytest.mark.parametrize(
    ("group", "bands"),
    [
        (
            "tier",
            ["tier-liquid", "tier-semi-liquid", "tier-illiquid", "unassigned"],
        ),
        (
            "institution",
            ["inst-ally", "inst-chase", "inst-x", "unassigned"],
        ),
        (
            "type",
            [
                "type-checking",
                "type-savings",
                "type-credit-card",
                "type-loan",
                "type-brokerage",
                "unassigned",
            ],
        ),
    ],
)
def test_group_by_rebands_the_rows(wide_client, group, bands):
    body = _get(wide_client, f"?group={group}")
    assert _bands(body) == bands
    # Five holdings and the (no account) row.
    assert len(_names(body)) == 6


def test_group_by_institution_labels_the_blank_band(wide_client):
    body = _get(wide_client, "?group=institution")
    assert "(no institution)" in body


def test_group_by_tier_puts_cards_with_cash(wide_client):
    body = _get(wide_client, "?group=tier")
    liquid = body.split('data-group="tier-liquid"')[1].split("</tbody>")[0]
    assert {"Chase Checking", "Ally Savings", "Chase Visa"} <= set(
        _names('id="projections-table"' + liquid)
    )


def test_group_by_none_is_one_flat_body(wide_client):
    body = _get(wide_client, "?group=none")
    assert "sheet-group--flat" in body
    table = body.split('id="projections-table"')[1]
    assert 'class="sheet-group-heading"' not in table
    # The pinned column header still carries the month links.
    thead = table.split("<thead>")[1].split("</thead>")[0]
    assert thead.count("data-header-link") == 12
    assert "(no account)" in body


@pytest.mark.parametrize(
    "query",
    [
        "?group=none",
        "?group=tier&view=change",
        f"?group=institution&from={_PAST}",
        "?group=type&type=loan",
    ],
)
def test_every_grouping_spans_the_full_grid(wide_client, query):
    parser = _RowSpans()
    parser.feed(_get(wide_client, query))
    assert parser.rows
    assert len(set(parser.rows)) == 1, parser.rows


def test_links_keep_filters_and_grouping(wide_client):
    body = _get(wide_client, "?group=tier&institution=Chase&type=checking")
    bar = body.split("data-bar-lead")[1].split("data-sheet-scroll")[0]
    # Every link but Reset (which clears the filters).
    links = [
        href
        for href, attrs in re.findall(r'href="([^"]+)"([^>]*)>', bar)
        if "data-filter-reset" not in attrs
    ]
    assert len(links) >= 6
    for link in links:
        assert "group=tier" in link
        assert "institution=Chase" in link
        assert "type=checking" in link
    pager = body.split("data-proj-window")[1].split("</div>")[0]
    for link in re.findall(r'href="([^"]+)"', pager):
        assert "group=tier" in link and "institution=Chase" in link
    # Month breakdown links in the column header carry them too.
    header = body.split('<tr class="sheet-group-header')[1].split("</tr>")[0]
    for link in re.findall(r'href="([^"]+)"', header):
        assert "group=tier" in link and "institution=Chase" in link


def test_filter_form_carries_the_page_state(wide_client):
    body = _get(wide_client, f"?months=24&view=change&estimate=1&from={_PAST}")
    form = body.split('id="projections-filter-form"')[1].split("</form>")[0]
    for name, value in (
        ("months", "24"),
        ("view", "change"),
        ("estimate", "1"),
        ("from", _PAST),
    ):
        assert f'<input type="hidden" name="{name}" value="{value}">' in form
    # Group and the filters come from the form's own controls, not hidden inputs.
    assert 'type="hidden" name="group"' not in form


def test_reset_keeps_the_grouping(wide_client):
    body = _get(wide_client, "?group=type&institution=Chase")
    reset = re.search(r'<a href="([^"]+)"\s+class="ml-auto', body).group(1)
    assert "group=type" in reset
    assert "institution" not in reset
