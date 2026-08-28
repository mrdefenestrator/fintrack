"""Test-only "kitchen-sink" sheet, mounted only when create_app(enable_sheet_demo=True).

It exercises the generic sheet renderer (web.sheets + partials/sheet_table.html)
across the full feature matrix — flat and grouped, every Column.kind, editable /
reorderable / deletable — independent of any real table's data. This gives the
framework two things:

* real blueprint endpoints so `url_for` resolves in the renderer unit tests
  (tests/web/test_sheets.py), and
* a live page for the framework e2e behaviour suite
  (tests/e2e/test_sheet_framework.py).

State lives in a per-process in-memory store so edits/reorders/deletes actually
mutate and re-render. Never registered in production.
"""

from __future__ import annotations

from copy import deepcopy

from flask import Blueprint, current_app, render_template

from web import sheets
from web.sheets import Cell, Column, Group, Row, TableSpec

sheet_demo_bp = Blueprint("sheet_demo", __name__, url_prefix="/_sheet_demo")

_COLOR_OPTIONS = [("red", "Red"), ("green", "Green"), ("blue", "Blue")]

_SEED_FLAT = [
    {"id": 1, "name": "Alpha", "amount": "1,200.00", "color": "red", "score": "10"},
    {"id": 2, "name": "Beta", "amount": "(340.00)", "color": "green", "score": "7"},
    {"id": 3, "name": "Gamma", "amount": "88.00", "color": "blue", "score": "3"},
]
_SEED_GROUPED = {
    "left": [{"id": 11, "name": "Left one", "amount": "500.00", "color": "red"}],
    "right": [{"id": 21, "name": "Right one", "amount": "(75.00)", "color": "blue"}],
}


def _store() -> dict:
    """Per-process mutable store, seeded on first use. Kept on the app config so
    a fresh test app starts from the seed."""
    store = current_app.config.get("_sheet_demo_store")
    if store is None:
        store = {"flat": deepcopy(_SEED_FLAT), "grouped": deepcopy(_SEED_GROUPED)}
        current_app.config["_sheet_demo_store"] = store
    return store


_FLAT_COLUMNS = [
    Column("name", "Name", kind=sheets.KIND_TEXT, editable=True, width="12rem"),
    Column("amount", "Amount", kind=sheets.KIND_CURRENCY, editable=True),
    Column(
        "color", "Color", kind=sheets.KIND_SELECT, editable=True, options_key="color"
    ),
    Column(
        "score", "Score", kind=sheets.KIND_NUMBER, editable=True, inputmode="numeric"
    ),
    Column("doubled", "Doubled", kind=sheets.KIND_COMPUTED),
]

_FLAT_ENDPOINTS = {
    "cell_edit": "sheet_demo.flat_cell_edit",
    "update": "sheet_demo.flat_update",
    "delete_confirm": "sheet_demo.flat_delete_confirm",
    "delete_btn": "sheet_demo.flat_delete_btn",
    "delete": "sheet_demo.flat_delete",
    "reorder": "sheet_demo.flat_reorder",
    "add": "sheet_demo.flat_add",
}


def flat_spec(
    editable=True, dom_id="demo-flat-table", row_id_prefix="demo-flat"
) -> TableSpec:
    return TableSpec(
        dom_id=dom_id,
        endpoints=_FLAT_ENDPOINTS,
        columns=_FLAT_COLUMNS,
        editable=editable,
        reorderable=True,
        deletable=True,
        options={"color": _COLOR_OPTIONS},
        row_id_prefix=row_id_prefix,
        footer=[["", "", "Total", "", ""]],
        empty_text="No items yet.",
    )


def locked_spec() -> TableSpec:
    """The flat sheet with editing off (edit-mode locked) — same data, so the
    framework's locked state (no add row, no actions column, no clickable cells)
    is exercised by the e2e suite."""
    return flat_spec(
        editable=False, dom_id="demo-locked-table", row_id_prefix="demo-locked"
    )


def _cell(display, *, raw=None, editable=False, options=None, negative=False):
    return Cell(
        display=display,
        raw=str(raw if raw is not None else display),
        editable=editable,
        options=options,
        is_negative=negative,
    )


def _flat_row(item: dict) -> Row:
    neg = item["amount"].startswith("(")
    try:
        doubled = f"{int(item['score']) * 2}"
    except ValueError:
        doubled = "-"
    return Row(
        params={"item_id": item["id"]},
        cells={
            "name": _cell(item["name"], editable=True),
            "amount": _cell(
                item["amount"],
                raw=item["amount"].strip("()"),
                editable=True,
                negative=neg,
            ),
            "color": _cell(item["color"].title(), raw=item["color"], editable=True),
            "score": _cell(item["score"], editable=True),
            "doubled": _cell(doubled),
        },
    )


def _flat_groups() -> list[Group]:
    rows = [_flat_row(i) for i in _store()["flat"]]
    return [
        Group(
            key="_",
            columns=_FLAT_COLUMNS,
            rows=rows,
            add_params={},
            add_noun="item",
            reorderable=True,
            empty_text="No items yet.",
        )
    ]


def _flat_body(**editing):
    ctx = sheets.render_context(flat_spec(), _flat_groups(), filename=None, **editing)
    return render_template("partials/sheet_body.html", **ctx)


# --------------------------------------------------------------------------- #
# Grouped kitchen sink
# --------------------------------------------------------------------------- #
_GROUPED_COLUMNS = [
    Column("name", "Name", kind=sheets.KIND_TEXT, editable=True),
    Column("amount", "Amount", kind=sheets.KIND_CURRENCY, editable=True),
    Column(
        "color", "Color", kind=sheets.KIND_SELECT, editable=True, options_key="color"
    ),
]

_GROUPED_ENDPOINTS = {
    "cell_edit": "sheet_demo.grouped_cell_edit",
    "update": "sheet_demo.grouped_update",
    "delete_confirm": "sheet_demo.grouped_delete_confirm",
    "delete_btn": "sheet_demo.grouped_delete_btn",
    "delete": "sheet_demo.grouped_delete",
    "reorder": "sheet_demo.grouped_reorder",
    "add": "sheet_demo.grouped_add",
}


def grouped_spec() -> TableSpec:
    return TableSpec(
        dom_id="demo-grouped-table",
        endpoints=_GROUPED_ENDPOINTS,
        grouped=True,
        editable=True,
        reorderable=True,
        deletable=True,
        options={"color": _COLOR_OPTIONS},
        row_id_prefix="demo-grouped",
        colgroup=["12rem", "9rem", "9rem"],
        subtotal_col=1,
        heading_label_span=1,
        footer=[["", "Net", ""]],
        footer_amount_pos=1,
    )


def _grouped_row(item: dict, gkey: str, accent: str) -> Row:
    neg = item["amount"].startswith("(")
    return Row(
        params={"gkey": gkey, "ref": item["id"]},
        accent=accent,
        cells={
            "name": _cell(item["name"], editable=True),
            "amount": _cell(
                item["amount"],
                raw=item["amount"].strip("()"),
                editable=True,
                negative=neg,
            ),
            "color": _cell(item["color"].title(), raw=item["color"], editable=True),
        },
    )


def _grouped_groups() -> list[Group]:
    data = _store()["grouped"]
    groups = []
    for key, label, accent in (
        ("left", "Left", "asset"),
        ("right", "Right", "liability"),
    ):
        rows = [_grouped_row(i, key, accent) for i in data[key]]
        total = "0.00"
        groups.append(
            Group(
                key=key,
                label=label,
                columns=_GROUPED_COLUMNS,
                rows=rows,
                subtotal=total,
                add_params={"gkey": key},
                add_noun=label,
                reorderable=True,
                empty_text=f"No {label} rows.",
            )
        )
    return groups


def _grouped_body(**editing):
    ctx = sheets.render_context(
        grouped_spec(), _grouped_groups(), filename=None, **editing
    )
    return render_template("partials/sheet_body.html", **ctx)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@sheet_demo_bp.route("/reset", methods=["POST"])
def reset():
    """Reseed the in-memory store (test isolation for the e2e suite)."""
    current_app.config["_sheet_demo_store"] = {
        "flat": deepcopy(_SEED_FLAT),
        "grouped": deepcopy(_SEED_GROUPED),
    }
    return "", 204


@sheet_demo_bp.route("/")
def demo_page():
    flat = sheets.render_context(flat_spec(), _flat_groups(), filename=None)
    grouped = sheets.render_context(grouped_spec(), _grouped_groups(), filename=None)
    locked = sheets.render_context(locked_spec(), _flat_groups(), filename=None)
    return render_template("sheet_demo.html", flat=flat, grouped=grouped, locked=locked)


def _find_flat(item_id: int):
    return next((i for i in _store()["flat"] if i["id"] == item_id), None)


@sheet_demo_bp.route("/flat/cell/<int:item_id>")
def flat_cell_edit(item_id: int):
    from flask import request

    if request.args.get("display") == "1":
        return _flat_body()
    field = request.args.get("field", "")
    return _flat_body(editing={"params": {"item_id": item_id}, "field": field})


@sheet_demo_bp.route("/flat/update/<int:item_id>", methods=["POST"])
def flat_update(item_id: int):
    from flask import request

    field = request.form.get("field", "")
    value = request.form.get("value", "")
    item = _find_flat(item_id)
    if item is not None and field in item:
        item[field] = value
    return _flat_body(updated={"params": {"item_id": item_id}, "field": field})


@sheet_demo_bp.route("/flat/delete-confirm/<int:item_id>")
def flat_delete_confirm(item_id: int):
    from flask import url_for

    return render_template(
        "partials/editable_table_delete_confirm.html",
        delete_url=url_for("sheet_demo.flat_delete", item_id=item_id),
        cancel_url=url_for("sheet_demo.flat_delete_btn", item_id=item_id),
    )


@sheet_demo_bp.route("/flat/delete-btn/<int:item_id>")
def flat_delete_btn(item_id: int):
    from flask import url_for

    return render_template(
        "partials/editable_table_delete_icon.html",
        delete_confirm_url=url_for("sheet_demo.flat_delete_confirm", item_id=item_id),
        edit_mode=True,
        reorderable=True,
    )


@sheet_demo_bp.route("/flat/delete/<int:item_id>", methods=["POST"])
def flat_delete(item_id: int):
    store = _store()
    store["flat"] = [i for i in store["flat"] if i["id"] != item_id]
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@sheet_demo_bp.route("/flat/reorder", methods=["POST"])
def flat_reorder():
    from flask import request

    order = [int(x) for x in request.form.get("order", "").split(",") if x != ""]
    flat = _store()["flat"]
    if sorted(order) == list(range(len(flat))):
        _store()["flat"] = [flat[i] for i in order]
    return "", 204


@sheet_demo_bp.route("/flat/add", methods=["POST"])
def flat_add():
    store = _store()
    new_id = max((i["id"] for i in store["flat"]), default=0) + 1
    store["flat"].append(
        {"id": new_id, "name": "New", "amount": "0.00", "color": "red", "score": "0"}
    )
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


def _find_grouped(gkey: str, ref: int):
    return next((i for i in _store()["grouped"].get(gkey, []) if i["id"] == ref), None)


@sheet_demo_bp.route("/grouped/cell/<gkey>/<int:ref>")
def grouped_cell_edit(gkey: str, ref: int):
    from flask import request

    if request.args.get("display") == "1":
        return _grouped_body()
    field = request.args.get("field", "")
    return _grouped_body(editing={"params": {"gkey": gkey, "ref": ref}, "field": field})


@sheet_demo_bp.route("/grouped/update/<gkey>/<int:ref>", methods=["POST"])
def grouped_update(gkey: str, ref: int):
    from flask import request

    field = request.form.get("field", "")
    value = request.form.get("value", "")
    item = _find_grouped(gkey, ref)
    if item is not None and field in item:
        item[field] = value
    return _grouped_body(updated={"params": {"gkey": gkey, "ref": ref}, "field": field})


@sheet_demo_bp.route("/grouped/delete-confirm/<gkey>/<int:ref>")
def grouped_delete_confirm(gkey: str, ref: int):
    from flask import url_for

    return render_template(
        "partials/editable_table_delete_confirm.html",
        delete_url=url_for("sheet_demo.grouped_delete", gkey=gkey, ref=ref),
        cancel_url=url_for("sheet_demo.grouped_delete_btn", gkey=gkey, ref=ref),
    )


@sheet_demo_bp.route("/grouped/delete-btn/<gkey>/<int:ref>")
def grouped_delete_btn(gkey: str, ref: int):
    from flask import url_for

    return render_template(
        "partials/editable_table_delete_icon.html",
        delete_confirm_url=url_for(
            "sheet_demo.grouped_delete_confirm", gkey=gkey, ref=ref
        ),
        edit_mode=True,
        reorderable=True,
    )


@sheet_demo_bp.route("/grouped/delete/<gkey>/<int:ref>", methods=["POST"])
def grouped_delete(gkey: str, ref: int):
    store = _store()
    store["grouped"][gkey] = [
        i for i in store["grouped"].get(gkey, []) if i["id"] != ref
    ]
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@sheet_demo_bp.route("/grouped/reorder/<gkey>", methods=["POST"])
def grouped_reorder(gkey: str):
    from flask import request

    order = [int(x) for x in request.form.get("order", "").split(",") if x != ""]
    rows = _store()["grouped"].get(gkey, [])
    if sorted(order) == list(range(len(rows))):
        _store()["grouped"][gkey] = [rows[i] for i in order]
    return "", 204


@sheet_demo_bp.route("/grouped/add/<gkey>", methods=["POST"])
def grouped_add(gkey: str):
    store = _store()
    rows = store["grouped"].setdefault(gkey, [])
    all_ids = [i["id"] for g in store["grouped"].values() for i in g]
    new_id = max(all_ids, default=0) + 1
    rows.append({"id": new_id, "name": "New", "amount": "0.00", "color": "red"})
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp
