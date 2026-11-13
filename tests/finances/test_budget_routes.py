"""Budget sheet (issue #51): a grouped sheet like Holdings — one band per
category with Remaining/Monthly/Annual subtotals in its heading, a pinned Net
footer, per-band add and drag-reorder."""

import re
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine

from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.db import init_db
from fintrack.ledger.repository.categories import add_category
from fintrack.snapshots.repository import create_snapshot

# Interleaved on purpose: grouping must collect each category's rows into its
# band while edit/delete URLs keep addressing the full-list position.
_ENTRIES = [
    ("income", "Salary", "Income", 4000),  # 0
    ("expense", "Rent", "Housing", 1500),  # 1
    ("expense", "Groceries", "Groceries", 600),  # 2
    ("expense", "Property Tax", "Housing", 300),  # 3
    ("expense", "Mystery", None, 10),  # 4
    ("income", "Rent refund", "Housing", 100),  # 5 (makes Housing mixed)
    ("expense", "Vet", "Pets", 50),  # 6 (not in the taxonomy)
]
# Taxonomy order deliberately not alphabetical.
_TAXONOMY = ["Income", "Housing", "Groceries"]


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        for name in _TAXONOMY:
            add_category(conn, name=name)
        sid = create_snapshot(conn, "home")
        for kind, desc, category, amount in _ENTRIES:
            entry = {
                "kind": kind,
                "description": desc,
                "amount": amount,
                "recurrence": "monthly",
                "dayOfMonth": 28,
            }
            if category:
                entry["category"] = category
            add_budget_entry(conn, sid, entry)
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _descriptions(db_engine) -> list[str]:
    with db_engine.connect() as conn:
        return [e["description"] for e in get_budget_entries(conn, 1)]


def _table(body: str) -> str:
    return body.split('id="budget-table"')[1].split("</table>")[0]


def _band_labels(body: str) -> list[str]:
    return re.findall(
        r'<tr class="sheet-group-heading">\s*<td colspan="5"[^>]*>([^<]+)</td>', body
    )


def test_bands_follow_category_order_with_uncategorized_last(client):
    """Taxonomy order first, then categories outside it (alphabetically), then
    entries with no category."""
    body = client.get("/s/home/budget").get_data(as_text=True)
    assert _band_labels(body) == _TAXONOMY + ["Pets", "Uncategorized"]
    # Grouped sheet chrome, no global thead.
    assert 'class="grouped-sheet' in body
    assert "<thead" not in _table(body)


def test_band_heading_subtotals_and_net_footer(client):
    body = client.get("/s/home/budget").get_data(as_text=True)
    housing = body.split('data-group="cat-housing"')[1].split("</tr>")[0]
    # Monthly and Annual subtotals: -1500 - 300 + 100 = -1700/mo.
    assert "($1,700.00)" in housing
    assert "($20,400.00)" in housing
    # Net monthly across everything: 4000 - 1500 - 600 - 300 - 10 + 100 - 50.
    footer = body.split('class="total-row')[1]
    assert ">Net<" in footer
    assert "$1,640.00" in footer


def test_band_color_per_category_in_taxonomy_order(client):
    """Each category gets its own hue, cycled in category order (taxonomy
    first, then other used categories); Uncategorized is slate. Rows carry no
    tone of their own, so their rails follow the band."""
    body = client.get("/s/home/budget").get_data(as_text=True)
    assert 'data-group="cat-income" data-tone="blue"' in body
    assert 'data-group="cat-housing" data-tone="amber"' in body
    assert 'data-group="cat-groceries" data-tone="violet"' in body
    assert 'data-group="cat-pets" data-tone="emerald"' in body
    assert 'data-group="uncategorized" data-tone="slate"' in body
    assert 'id="budget-row-5" data-kind="income" data-reorder-index' in body


def test_band_color_survives_filtering(client):
    """A category keeps its hue when filters hide the bands before it."""
    body = client.get("/s/home/budget?include=Groceries").get_data(as_text=True)
    assert 'data-group="cat-groceries" data-tone="violet"' in body


def test_no_kind_column(client):
    """Kind is shown by the amount's sign; there is no Kind column."""
    body = client.get("/s/home/budget?edit=1").get_data(as_text=True)
    header = body.split('class="sheet-group-header')[1].split("</tr>")[0]
    assert ">Kind " not in header
    assert ">Category " in header
    view = client.get("/s/home/budget").get_data(as_text=True)
    row = view.split('id="budget-row-0"')[1].split("</tr>")[0]
    assert row.count("<td") == 10  # Category … Auto account


def test_add_row_leads_with_band_majority_kind(client):
    body = client.get("/s/home/budget?edit=1").get_data(as_text=True)
    income = body.split('data-group="cat-income"')[1].split("</tbody>")[0]
    housing = body.split('data-group="cat-housing"')[1].split("</tbody>")[0]
    add_income, add_expense = "+ Add income", "+ Add expense"
    assert income.index(add_income) < income.index(add_expense)
    assert housing.index(add_expense) < housing.index(add_income)


def test_rows_keep_full_list_index_and_local_reorder_index(client):
    body = client.get("/s/home/budget?edit=1").get_data(as_text=True)
    # Property Tax is entry 3 overall but the 2nd row of its band.
    assert re.search(r'id="budget-row-3"[^>]*data-reorder-index="1"', body)
    assert "/budget/cell/3?field=description" in body


def test_actions_column_only_in_edit_mode(client):
    view = _table(client.get("/s/home/budget").get_data(as_text=True))
    edit = client.get("/s/home/budget?edit=1").get_data(as_text=True)
    assert "table-actions-cell" not in view
    assert "data-reorder-url" not in view
    assert "table-actions-cell" in edit
    assert "/budget/reorder?category=Housing" in edit
    assert "data-sticky-actions" in edit


def test_category_filter_drops_other_bands_and_locks_reorder(client):
    body = client.get("/s/home/budget?edit=1&include=Groceries").get_data(as_text=True)
    assert _band_labels(body) == ["Groceries"]
    assert "data-reorder-locked" in body


def test_filter_matching_nothing_shows_message(client):
    body = client.get("/s/home/budget?include_recurrence=annual").get_data(as_text=True)
    assert "No entries match the current filters." in body
    assert _band_labels(body) == []


def test_reorder_within_band_leaves_other_rows(client, db_engine):
    # Housing rows sit at 1, 3, 5: move the refund (local 2) to the top.
    resp = client.post(
        "/s/home/budget/reorder?category=Housing", data={"order": "2,0,1"}
    )
    assert resp.status_code == 204
    assert _descriptions(db_engine) == [
        "Salary",
        "Rent refund",
        "Groceries",
        "Rent",
        "Mystery",
        "Property Tax",
        "Vet",
    ]


def test_reorder_uncategorized_band(client, db_engine):
    resp = client.post("/s/home/budget/reorder?category=", data={"order": "0"})
    assert resp.status_code == 204
    assert _descriptions(db_engine)[4] == "Mystery"


@pytest.mark.parametrize("order", ["0,1", "0,0,1", "x", ""])
def test_reorder_rejects_non_permutation(client, db_engine, order):
    before = _descriptions(db_engine)
    resp = client.post("/s/home/budget/reorder?category=Housing", data={"order": order})
    assert resp.status_code == 422
    assert _descriptions(db_engine) == before


def test_add_files_entry_under_band_category(client, db_engine):
    resp = client.post(
        "/s/home/budget/add", data={"kind": "income", "category": "Housing"}
    )
    assert resp.headers.get("HX-Refresh") == "true"
    with db_engine.connect() as conn:
        new = get_budget_entries(conn, 1)[-1]
    assert (new["kind"], new.get("category")) == ("income", "Housing")


def test_add_ignores_unknown_category(client, db_engine):
    client.post("/s/home/budget/add", data={"kind": "expense", "category": "Nope"})
    with db_engine.connect() as conn:
        new = get_budget_entries(conn, 1)[-1]
    assert new.get("category") is None


def test_add_accepts_category_already_in_use(client, db_engine):
    """A band for a category outside the taxonomy still adds into itself."""
    client.post("/s/home/budget/add", data={"kind": "expense", "category": "Pets"})
    with db_engine.connect() as conn:
        new = get_budget_entries(conn, 1)[-1]
    assert new.get("category") == "Pets"


def test_cell_edit_swaps_whole_grouped_body(client):
    body = client.get("/s/home/budget/cell/1?field=description").get_data(as_text=True)
    assert "<colgroup>" in body
    assert body.count('class="sheet-group"') == 5
    assert 'hx-target="#budget-table"' in body
    assert 'name="value" value="Rent"' in body


def test_recategorizing_moves_row_to_its_new_band(client):
    body = client.post(
        "/s/home/budget/update/2", data={"field": "category", "value": "Housing"}
    ).get_data(as_text=True)
    housing = body.split('data-group="cat-housing"')[1].split("</tbody>")[0]
    assert 'id="budget-row-2"' in housing
    assert 'data-group="cat-groceries"' not in body


# --- Group by (issue #137) -------------------------------------------------


def test_group_by_kind_bands_are_neutral_and_rows_keep_category_color(client):
    body = client.get("/s/home/budget?group=kind&edit=1").get_data(as_text=True)
    assert _band_labels(body) == ["Income", "Expense"]
    assert 'data-group="kind-income" data-tone="slate"' in body
    assert 'data-group="kind-expense" data-tone="slate"' in body
    income = body.split('data-group="kind-income"')[1].split("</tbody>")[0]
    assert 'id="budget-row-0"' in income and 'id="budget-row-5"' in income
    # A row's own rail keeps its category's hue (Housing is amber).
    assert re.search(r'id="budget-row-5" data-kind="income" data-tone="amber"', body)
    # The stored order is per category, so kind bands don't drag-reorder;
    # a band adds only its own kind.
    assert "data-reorder-url" not in body
    assert "+ Add expense" not in income
    # Subtotals and the Net footer don't depend on the grouping.
    footer = body.split('class="total-row')[1]
    assert "$1,640.00" in footer


def test_group_by_recurrence_adds_with_the_band_recurrence(client, db_engine):
    client.post(
        "/s/home/budget/update/2", data={"field": "recurrence", "value": "weekly"}
    )
    body = client.get("/s/home/budget?group=recurrence&edit=1").get_data(as_text=True)
    assert _band_labels(body) == ["Weekly", "Monthly"]
    weekly = body.split('data-group="rec-weekly"')[1].split("</tbody>")[0]
    assert '"recurrence": "weekly"' in weekly
    client.post("/s/home/budget/add", data={"kind": "expense", "recurrence": "weekly"})
    with db_engine.connect() as conn:
        assert get_budget_entries(conn, 1)[-1]["recurrence"] == "weekly"


def test_group_by_none_is_one_flat_reorderable_body(client, db_engine):
    body = client.get("/s/home/budget?group=none&edit=1").get_data(as_text=True)
    table = _table(body)
    assert _band_labels(body) == []
    assert "<thead>" in table
    assert table.count('class="sheet-group sheet-group--flat"') == 1
    assert table.count('id="budget-row-') == len(_ENTRIES)
    # (The URL also carries the viewed month, ym — issue #136.)
    assert 'data-reorder-url="/s/home/budget/reorder?all=1' in table
    order = ",".join(str(i) for i in reversed(range(len(_ENTRIES))))
    assert (
        client.post("/s/home/budget/reorder?all=1", data={"order": order}).status_code
        == 204
    )
    assert _descriptions(db_engine) == [d for _, d, _, _ in reversed(_ENTRIES)]


def test_unknown_group_falls_back_to_category(client):
    body = client.get("/s/home/budget?group=bogus").get_data(as_text=True)
    assert _band_labels(body) == _TAXONOMY + ["Pets", "Uncategorized"]


def test_edit_swap_keeps_the_page_grouping(client):
    body = client.get(
        "/s/home/budget/cell/1?field=description",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost/s/home/budget?group=kind&edit=1",
        },
    ).get_data(as_text=True)
    assert 'data-group="kind-expense"' in body
    assert 'data-group="cat-housing"' not in body


def test_group_by_control_and_reset_keep_the_grouping(client):
    body = client.get("/s/home/budget?group=kind&include=Housing").get_data(
        as_text=True
    )
    assert "data-group-by-label>Kind<" in body
    assert 'name="group" value="kind" checked' in body
    reset = re.search(r'<a href="([^"]+)"[^>]*>Reset</a>', body).group(1)
    assert "group=kind" in reset and "include" not in reset


@pytest.mark.parametrize("page", ["budget", "holdings"])
def test_band_css_ships_inline_with_the_page(client, page):
    """The band chrome (tints, rails, sticky heading/header) must arrive with
    the HTML: the PR-preview Lambda (reserved concurrency 1) 429s parallel
    subresource requests, so a <link>ed stylesheet could silently drop."""
    body = client.get(f"/s/home/{page}").get_data(as_text=True)
    head = body.split("</head>")[0]
    assert '.grouped-sheet [data-tone="emerald"]' in head
    assert ".grouped-sheet .sheet-group-heading > td" in head
    assert "grouped-sheet.css" not in body  # no separate stylesheet request


class _RowSpans(HTMLParser):
    """Sum each <tr>'s cell colspans inside #budget-table / #holdings-table."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.rows: list[tuple[str, int]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and a.get("id") in ("budget-table", "holdings-table"):
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.rows.append((a.get("class") or "", 0))
        elif self.in_table and tag in ("td", "th") and self.rows:
            cls, n = self.rows[-1]
            self.rows[-1] = (cls, n + int(a.get("colspan") or 1))

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False


@pytest.mark.parametrize(
    "page",
    ["budget", "budget?group=kind", "budget?group=recurrence", "holdings"],
)
@pytest.mark.parametrize("edit", [False, True])
def test_every_row_spans_the_full_grid(client, page, edit):
    """Every row (band heading, column header, data, add, footer) spans the same
    number of grid slots, and in edit mode each heading closes with its own
    sticky actions cell — otherwise heading content paints over the pinned
    actions column when the sheet scrolls sideways."""
    sep = "&" if "?" in page else "?"
    body = client.get(f"/s/home/{page}{sep + 'edit=1' if edit else ''}").get_data(
        as_text=True
    )
    parser = _RowSpans()
    parser.feed(body)
    spans = {n for _, n in parser.rows}
    assert len(spans) == 1, parser.rows
    headings = body.split('<tr class="sheet-group-heading">')[1:]
    assert headings
    for h in headings:
        assert ("table-actions-cell" in h.split("</tr>")[0]) is edit


def test_drag_handles_only_where_bands_reorder(client):
    by_kind = client.get("/s/home/budget?group=kind&edit=1").get_data(as_text=True)
    by_cat = client.get("/s/home/budget?edit=1").get_data(as_text=True)
    assert "drag-handle" not in _table(by_kind)
    assert "drag-handle" in _table(by_cat)
    cancel = client.get(
        "/s/home/budget/delete-btn/1",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost/s/home/budget?group=kind&edit=1",
        },
    ).get_data(as_text=True)
    assert "drag-handle" not in cancel
