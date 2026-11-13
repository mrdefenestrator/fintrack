"""Shared grouped-sheet helpers (web/routes/grouped_sheet.py), used by the
Holdings and Budget sheets."""

import pytest

from web.routes.grouped_sheet import (
    SubField,
    compound_column,
    group_columns,
    parse_order,
    remap_group_order,
)


def test_group_columns_metadata():
    cols = [("a", "A", False, 1), ("b", "B", True, 2), ("c", "C", True, 1)]
    meta = group_columns(cols, {"b": "Bee"})
    assert meta == {
        "col_keys": ["a", "b", "c"],
        "headers": ["A", "B", "C"],
        "tooltips": ["A", "Bee", "C"],
        "right_align": [1, 2],
        "spans": [1, 2, 1],
        "ncols": 3,
        "compound": {},
    }


def test_compound_column_places_fields_by_line_and_track():
    """Holdings' Details: fields keyed, each placed on its own grid line/track
    (None leaves a track empty), on one shared template."""
    layout = compound_column(
        [
            [SubField("x", "X", True), None],
            [SubField("y", "Y", False), SubField("z", "Z", True)],
        ],
        ["4rem", "6rem"],
        {"z": "Zed"},
    )
    assert layout["template"] == "4rem 6rem"
    assert layout["lines"] == 2
    assert layout["keys"] == ["x", "y", "z"]
    assert [(f["key"], f["row"], f["col"]) for f in layout["fields"]] == [
        ("x", 1, 1),
        ("y", 2, 1),
        ("z", 2, 2),
    ]
    assert layout["fields"][2]["tooltip"] == "Zed"
    meta = group_columns([("d", "D", False, 1)], compound={"d": layout})
    assert meta["compound"]["d"] is layout


def test_compound_column_rejects_a_short_line():
    with pytest.raises(ValueError):
        compound_column([[SubField("x", "X", True)]], ["4rem", "6rem"])


def test_parse_order():
    assert parse_order("2,0,1") == [2, 0, 1]
    assert parse_order("") == []
    assert parse_order("1,x") is None


def test_remap_moves_only_group_slots():
    items = ["a1", "b1", "a2", "b2", "a3"]
    # Reverse the a-group (positions 0, 2, 4); b rows stay put.
    full = remap_group_order(items, lambda s: s[0] == "a", [2, 1, 0])
    assert full == [4, 1, 2, 3, 0]
    assert [items[i] for i in full] == ["a3", "b1", "a2", "b2", "a1"]


def test_remap_rejects_non_permutation():
    items = ["a", "b", "a"]

    def in_a(s):
        return s == "a"

    assert remap_group_order(items, in_a, [0]) is None
    assert remap_group_order(items, in_a, [0, 0]) is None
    assert remap_group_order(items, in_a, [0, 2]) is None


def test_selected_group_reads_query_then_htmx_page_url():
    from flask import Flask

    from web.routes.grouped_sheet import GroupBy, selected_group

    opts = [GroupBy("a", "A"), GroupBy("b", "B")]
    app = Flask(__name__)
    with app.test_request_context("/x?group=b"):
        assert selected_group(opts) == "b"
    with app.test_request_context("/x?group=nope"):
        assert selected_group(opts) == "a"
    with app.test_request_context(
        "/x/cell",
        headers={"HX-Request": "true", "HX-Current-URL": "http://h/x?group=b"},
    ):
        assert selected_group(opts) == "b"
    # Only htmx requests fall back to the page URL.
    with app.test_request_context(
        "/x", headers={"HX-Current-URL": "http://h/x?group=b"}
    ):
        assert selected_group(opts) == "a"


def test_band_keys_and_category_tones():
    from web.routes.grouped_sheet import (
        CATEGORY_TONES,
        band_key,
        category_order,
        category_tones,
        unique_keys,
    )

    assert band_key("cat", "Food & Drink") == "cat-food-drink"
    assert band_key("cat", "!!") == "cat-x"
    assert unique_keys(["a", "a", "b"]) == ["a", "a-", "b"]
    assert category_order(["Z", "A"], ["B", None, "A", ""]) == ["Z", "A", "B"]
    tones = category_tones([f"c{i}" for i in range(len(CATEGORY_TONES) + 1)])
    assert tones["c0"] == tones[f"c{len(CATEGORY_TONES)}"] == CATEGORY_TONES[0]
