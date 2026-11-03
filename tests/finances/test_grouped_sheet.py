"""Shared grouped-sheet helpers (web/routes/grouped_sheet.py), used by the
Holdings and Budget sheets."""

from web.routes.grouped_sheet import group_columns, parse_order, remap_group_order


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
    }


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
