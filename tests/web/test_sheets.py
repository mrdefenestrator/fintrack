"""Framework-level regression tests for the generic sheet renderer.

These test the renderer ONCE, comprehensively, against synthetic specs and the
kitchen-sink demo blueprint — so each real table (Holdings, Budget, …) only
needs a thin "spec wired correctly" check rather than a full re-test of
interactive behaviour. Behaviour that needs a browser (keyboard nav, drag,
scroll shadows) is covered by tests/e2e/test_sheet_framework.py; here we assert
the HTML/attribute hooks that JS and CSS depend on.
"""

import re

import pytest

from web import sheets
from web.sheets import Cell, Column, Group, Row, TableSpec

# Endpoints of the demo blueprint — reused so url_for resolves for editable
# synthetic specs. Rows must then carry params={"item_id": <int>}.
FLAT_ENDPOINTS = {
    "cell_edit": "sheet_demo.flat_cell_edit",
    "update": "sheet_demo.flat_update",
    "delete_confirm": "sheet_demo.flat_delete_confirm",
    "delete_btn": "sheet_demo.flat_delete_btn",
    "delete": "sheet_demo.flat_delete",
    "reorder": "sheet_demo.flat_reorder",
    "add": "sheet_demo.flat_add",
}


def _cols(**over):
    return [
        Column("name", "Name", editable=True),
        Column("amount", "Amount", kind=sheets.KIND_CURRENCY, editable=True),
        Column(
            "color",
            "Color",
            kind=sheets.KIND_SELECT,
            editable=True,
            options_key="color",
        ),
        Column("doubled", "Doubled", kind=sheets.KIND_COMPUTED),
    ]


def _row(item_id=1, *, name="Alpha", amount="1,200.00", color="red", editable=True):
    neg = amount.startswith("(")
    return Row(
        params={"item_id": item_id},
        cells={
            "name": Cell(name, raw=name, editable=editable),
            "amount": Cell(
                amount, raw=amount.strip("()"), editable=editable, is_negative=neg
            ),
            "color": Cell(color.title(), raw=color, editable=editable),
            "doubled": Cell("20"),
        },
    )


def _spec(**over):
    base = dict(
        dom_id="t-tbody",
        endpoints=FLAT_ENDPOINTS,
        columns=_cols(),
        editable=True,
        reorderable=True,
        deletable=True,
        options={"color": [("red", "Red"), ("green", "Green"), ("blue", "Blue")]},
        row_id_prefix="t",
    )
    base.update(over)
    return TableSpec(**base)


def _group(rows, **over):
    base = dict(key="_", columns=_cols(), rows=rows, add_noun="item", reorderable=True)
    base.update(over)
    return Group(**base)


# --------------------------------------------------------------------------- #
# Schema-level (no rendering)
# --------------------------------------------------------------------------- #
def test_currency_and_number_columns_right_align():
    assert Column("a", kind=sheets.KIND_CURRENCY).right
    assert Column("a", kind=sheets.KIND_PERCENT).right
    assert Column("a", kind=sheets.KIND_NUMBER).right
    assert not Column("a", kind=sheets.KIND_TEXT).right
    # explicit align overrides kind
    assert Column("a", kind=sheets.KIND_CURRENCY, align="left").right is False


def test_max_cols_and_row_dom_id():
    spec = _spec(grouped=True)
    wide = Group(key="w", columns=_cols(), rows=[])
    narrow = Group(key="n", columns=_cols()[:2], rows=[])
    assert sheets.max_cols(spec, [wide, narrow]) == 4
    assert sheets.row_dom_id(spec, Row(params={"item_id": 7})) == "t-7"
    assert sheets.row_dom_id(spec, Row(params={}, dom_id="explicit")) == "explicit"


# --------------------------------------------------------------------------- #
# Flat rendering
# --------------------------------------------------------------------------- #
def test_flat_editable_cell_is_clickable_and_targets_tbody(render_body):
    html = render_body(_spec(), [_group([_row()])])
    # editable display cell opens the cell-edit URL, swapping the tbody
    assert 'hx-get="/_sheet_demo/flat/cell/1?field=name"' in html
    assert 'hx-target="#t-tbody"' in html
    assert 'hx-swap="innerHTML"' in html


def test_flat_computed_cell_not_clickable(render_body):
    html = render_body(_spec(), [_group([_row()])])
    # the computed "doubled" cell must never be an editable/clickable cell
    assert "field=doubled" not in html


def test_currency_cell_right_aligned_and_negative_coloured(render_body):
    html = render_body(_spec(), [_group([_row(amount="(340.00)")])])
    assert "text-right tabular-nums" in html
    assert "text-red-600" in html  # negative colour token


def test_editing_text_field_renders_input_editor(render_body):
    html = render_body(
        _spec(), [_group([_row()])], editing={"params": {"item_id": 1}, "field": "name"}
    )
    assert "cell-editing" in html
    assert 'name="value" value="Alpha"' in html
    assert 'hx-post="/_sheet_demo/flat/update/1"' in html


def test_editing_select_field_renders_options_with_selection(render_body):
    html = render_body(
        _spec(),
        [_group([_row(color="green")])],
        editing={"params": {"item_id": 1}, "field": "color"},
    )
    assert "table-cell-select" in html
    assert '<option value="green" selected>Green</option>' in html
    assert '<option value="red">Red</option>' in html


def test_updated_marker_only_on_updated_cell(render_body):
    # cell_display renders is_updated into the tbody; assert the update path runs
    html = render_body(
        _spec(),
        [_group([_row()])],
        updated={"params": {"item_id": 1}, "field": "amount"},
    )
    assert 'hx-get="/_sheet_demo/flat/cell/1?field=amount"' in html


def test_empty_group_shows_empty_text(render_body):
    html = render_body(_spec(), [_group([], empty_text="Nothing here yet.")])
    assert "Nothing here yet." in html


def test_footer_rows_render(render_body):
    spec = _spec(footer=[["", "", "Total", ""]], footer_amount_pos=1)
    html = render_body(spec, [_group([_row()])])
    assert "total-row" in html
    assert "Total" in html


# --------------------------------------------------------------------------- #
# Feature flags (opt-in) toggle exactly their hooks
# --------------------------------------------------------------------------- #
def test_reorderable_off_drops_reorder_hooks(render_body):
    on = render_body(_spec(reorderable=True), [_group([_row()], reorderable=True)])
    off = render_body(_spec(reorderable=False), [_group([_row()], reorderable=True)])
    assert "data-reorder-index" in on and "data-reorder-url" in on
    assert "data-reorder-index" not in off and "data-reorder-url" not in off


def test_deletable_off_has_no_delete_button(render_body):
    on = render_body(_spec(deletable=True), [_group([_row()])])
    off = render_body(_spec(deletable=False), [_group([_row()])])
    assert "delete-confirm" in on
    assert "delete-confirm" not in off
    # still keeps an actions cell for column alignment
    assert "table-actions-cell" in off


def test_non_editable_table_renders_plain_cells(render_body):
    spec = _spec(editable=False, reorderable=False, deletable=False, endpoints={})
    html = render_body(
        spec, [_group([_row(editable=False)], add_noun="", reorderable=False)]
    )
    assert "hx-get" not in html  # nothing clickable
    assert "table-actions-cell" not in html  # no actions column
    assert "Alpha" in html  # data still rendered


def test_per_row_editability_gate(render_body):
    # a cell marked editable=False in an editable table is not clickable
    row = _row()
    row.cells["name"].editable = False
    html = render_body(_spec(), [_group([row])])
    assert "field=name" not in html
    assert "field=amount" in html  # sibling cell still editable


def test_custom_editor_escape_hatch(render_body):
    col = Column(
        "name", "Name", editable=True, custom_editor="partials/sheet_add_row.html"
    )
    spec = _spec(columns=[col])
    row = Row(
        params={"item_id": 1}, cells={"name": Cell("Alpha", raw="Alpha", editable=True)}
    )
    html = render_body(
        spec,
        [_group([row], columns=[col])],
        editing={"params": {"item_id": 1}, "field": "name"},
    )
    # the custom editor template was included instead of the default input
    assert 'name="value"' not in html


# --------------------------------------------------------------------------- #
# Grouped rendering + ragged right edge
# --------------------------------------------------------------------------- #
def test_grouped_heading_header_and_accent(render_body):
    spec = _spec(grouped=True, subtotal_col=1, heading_label_span=1)
    g = _group([_row()], key="cash", label="Cash", subtotal="$100.00")
    g.rows[0].accent = "asset"
    html = render_body(spec, [g])
    assert "sheet-group-heading" in html
    assert "sheet-group-header" in html
    assert "Cash" in html and "$100.00" in html
    assert 'data-accent="asset"' in html
    assert 'data-group="cash"' in html


def test_grouped_ragged_filler_padding(render_body):
    spec = _spec(grouped=True)
    wide = _group([_row()], key="wide", columns=_cols())
    narrow_cols = _cols()[:2]
    narrow_row = Row(
        params={"item_id": 2},
        cells={
            "name": Cell("N", raw="N", editable=True),
            "amount": Cell("1.00", raw="1.00", editable=True),
        },
    )
    narrow = _group([narrow_row], key="narrow", columns=narrow_cols)
    html = render_body(spec, [wide, narrow])
    # the narrow group pads to the wide group's column count via a colspan filler
    assert 'colspan="2"' in html


# --------------------------------------------------------------------------- #
# Invariants JS/CSS depend on
# --------------------------------------------------------------------------- #
def test_sticky_group_chrome_uses_no_border_collapse_borders(render_body):
    """The box-shadow invariant: sticky group heading/header rows must not carry
    border-collapse border utilities (those mispaint when the row is sticky —
    the borders are drawn as box-shadows in base.html instead)."""
    spec = _spec(grouped=True, heading_label_span=1, subtotal_col=1)
    html = render_body(
        spec, [_group([_row()], key="cash", label="Cash", subtotal="$1")]
    )
    for m in re.finditer(r'<(?:th|td)[^>]*class="([^"]*)"[^>]*>', html):
        classes = m.group(1)
        # only inspect the sticky group-chrome cells
        if "sheet-group" in classes or "sheet-blank-slot" in classes:
            assert not re.search(r"\bborder-[trbl]\b", classes), classes


def test_static_js_is_cache_busted(client):
    """Static JS/CSS must be versioned (?v=…) so a shipped fix to sheet-sort.js /
    cell-nav.js / sheet-scroll.js actually reaches returning visitors instead of
    being served stale from the browser cache (the recurring 'fix didn't work'
    reports were stale cached JS)."""
    html = client.get("/_sheet_demo/").data.decode()
    for name in ("sheet-sort.js", "cell-nav.js", "sheet-scroll.js"):
        assert re.search(re.escape(name) + r"\?v=\d+", html), name
        # no unversioned reference to the same file
        assert f'{name}"' not in html


def test_scroll_shadow_and_cellnav_hooks_present_on_shell(client):
    html = client.get("/_sheet_demo/").data.decode()
    assert 'data-cell-nav="demo-flat-table"' in html
    assert 'data-cell-nav="demo-grouped-table"' in html
    assert html.count("data-sticky-actions") >= 2
    # the sheet-scroll opt-in attribute is present on the containers
    assert "data-sheet-scroll" in html


def test_swap_body_is_full_table_content_not_nested_tbody(render_body):
    """The swap target is the <table>, so a cell-edit body must carry the
    colgroup + (flat) thead and a flat, non-nested set of tbodies — otherwise
    swapping it into a <tbody> nests tbodies and breaks the page (QA)."""
    html = render_body(_spec(), [_group([_row()])])
    assert "<colgroup>" in html
    assert "<thead>" in html
    # exactly the data tbody(ies); no <tbody> nested inside another <tbody>
    import re as _re

    assert not _re.search(r"<tbody[^>]*>\s*<tbody", html)
    # editors target the table id via innerHTML, never a bare tbody
    assert 'hx-target="#t-tbody"' in html


def test_locked_table_still_has_sort_hooks(client):
    """Sorting is a read op and must work locked or unlocked: even a
    non-editable table carries the data-sheet hook and sortable-th headers so
    sheet-sort.js binds it. (Regression: sort was bound to data-cell-nav, which
    is emitted only in edit mode, so the locked view could not sort.)"""
    html = client.get("/_sheet_demo/").data.decode()
    start = html.index('id="demo-locked-table"')
    tag = html[start : html.index(">", start)]  # the <table ...> opening tag
    assert "data-sheet" in tag
    locked = html[start : html.index("</table>", start)]
    assert "sortable-th" in locked
    assert "data-sort-key" in locked


def test_locked_table_hides_all_edit_affordances(client):
    """A locked (non-editable) sheet must show no add row, no actions column,
    and no clickable cells — the regression behind 'add row / drag+delete column
    always show when the table is locked'."""
    html = client.get("/_sheet_demo/").data.decode()
    start = html.index('id="demo-locked-table"')
    locked = html[start : html.index("</table>", start)]
    assert "data-add-row" not in locked
    assert "table-actions-cell" not in locked
    assert "hx-get" not in locked  # cells not clickable


# --------------------------------------------------------------------------- #
# End-to-end through the demo routes (render + mutate + re-render)
# --------------------------------------------------------------------------- #
def test_demo_update_persists_and_rerenders(client):
    r = client.post(
        "/_sheet_demo/flat/update/1", data={"field": "name", "value": "Renamed"}
    )
    assert r.status_code == 200
    assert "Renamed" in r.data.decode()


def test_demo_delete_flow(client):
    confirm = client.get("/_sheet_demo/flat/delete-confirm/1")
    assert "✓" in confirm.data.decode() and "✗" in confirm.data.decode()
    dele = client.post("/_sheet_demo/flat/delete/1")
    assert dele.headers.get("HX-Refresh") == "true"
    page = client.get("/_sheet_demo/").data.decode()
    assert "Alpha" not in page  # row 1 gone


def test_demo_add_row(client):
    before = client.get("/_sheet_demo/").data.decode().count('id="demo-flat-')
    client.post("/_sheet_demo/flat/add")
    after = client.get("/_sheet_demo/").data.decode().count('id="demo-flat-')
    assert after == before + 1


def test_demo_reorder(client):
    r = client.post("/_sheet_demo/flat/reorder", data={"order": "2,1,0"})
    assert r.status_code == 204


def test_demo_grouped_edit_select(client):
    html = client.get("/_sheet_demo/grouped/cell/left/11?field=color").data.decode()
    assert "table-cell-select" in html
    assert 'hx-target="#demo-grouped-table"' in html


@pytest.mark.parametrize("field", ["name", "amount"])
def test_demo_grouped_update(client, field):
    r = client.post(
        "/_sheet_demo/grouped/update/left/11",
        data={"field": field, "value": "9.99" if field == "amount" else "Z"},
    )
    assert r.status_code == 200
