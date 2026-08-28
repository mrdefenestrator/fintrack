"""Category management — the dedicated "Categories" page.

Categories are a deliberately global table (not scoped to a snapshot, per
CLAUDE.md), but the page lives under the snapshot-scoped /s/<filename> prefix
purely for URL / navigation consistency (url_for needs `filename` to build
links back into the current snapshot, and the shared chrome + sidebar are
snapshot-scoped). None of the repository calls below use snapshot_id.

The page is rendered by the generic data-driven sheet renderer (web.sheets):
one editable "Category" column with click-to-edit rename, an inline delete
confirm in the actions column, and a trailing add row. Every add / rename /
delete re-renders the tbody (#categories-tbody); an error (duplicate name,
in-use delete) comes back as a 422 carrying the same tbody plus an inline error
row (base.html's htmx:beforeSwap hook swaps non-empty 422 bodies in).
"""

from flask import Blueprint, current_app, g, render_template, request, url_for

from web import sheets
from web.routes.common import snapshot_scoped
from web.sheets import Cell, Column, Group, Row, TableSpec

from fintrack.ledger.repository.categories import (
    add_category,
    delete_category,
    list_categories,
    rename_category,
)

bp = snapshot_scoped(Blueprint("categories", __name__, url_prefix="/s/<filename>"))

_ENDPOINTS = {
    "cell_edit": "categories.cell_edit",
    "update": "categories.rename",
    "delete_confirm": "categories.delete_confirm",
    "delete_btn": "categories.delete_btn",
    "delete": "categories.delete",
    "add": "categories.add",
}


def _spec(cats) -> TableSpec:
    label = f"{len(cats)} categor{'ies' if len(cats) != 1 else 'y'}"
    return TableSpec(
        dom_id="categories-tbody",
        endpoints=_ENDPOINTS,
        columns=[Column("name", "Category", editable=True)],
        editable=True,
        deletable=True,
        reorderable=False,
        row_id_prefix="category-row",
        footer=[[label]] if cats else None,
        empty_text="No categories yet.",
        container_class=(
            "rounded-lg border border-gray-300 dark:border-gray-600 shadow-sm"
        ),
    )


def _group(cats) -> Group:
    rows = [
        Row(
            params={"category_id": c["id"]},
            dom_id=f"category-row-{c['id']}",
            cells={"name": Cell(c["name"], raw=c["name"], editable=True)},
        )
        for c in cats
    ]
    return Group(
        key="_", rows=rows, add_noun="category", empty_text="No categories yet."
    )


def _tbody(error=None, editing_id=None, status=200):
    """Render the categories tbody (the swap target of every mutation)."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        cats = list_categories(conn)
    editing = (
        {"params": {"category_id": editing_id}, "field": "name"}
        if editing_id is not None
        else None
    )
    ctx = sheets.render_context(
        _spec(cats), [_group(cats)], filename=g.filename, editing=editing, error=error
    )
    html = render_template("partials/sheet_body.html", **ctx)
    return html if status == 200 else (html, status)


@bp.route("/categories")
def index():
    """The Categories page (full chrome)."""
    edit_mode = request.args.get("edit") == "1"
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        cats = list_categories(conn)
    spec = _spec(cats)
    ctx = sheets.render_context(spec, [_group(cats)], filename=g.filename)
    return render_template(
        "categories.html", active_tab="categories", edit_mode=edit_mode, **ctx
    )


@bp.route("/categories/add", methods=["POST"])
def add():
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        existing = {c["name"].lower() for c in list_categories(conn)}
        name = "New category"
        if name.lower() in existing:
            n = 2
            while f"{name} {n}".lower() in existing:
                n += 1
            name = f"{name} {n}"
        add_category(conn, name=name)
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


@bp.route("/categories/<int:category_id>/cell")
def cell_edit(category_id):
    """Switch one row into its inline rename editor, or revert on ?display=1."""
    if request.args.get("display") == "1":
        return _tbody()
    return _tbody(editing_id=category_id)


@bp.route("/categories/<int:category_id>/rename", methods=["POST"])
def rename(category_id):
    # `value` is the spreadsheet cell input name (macros/table.html); `name`
    # is kept as a fallback for direct/non-spreadsheet callers.
    new_name = request.form.get("value", request.form.get("name", "")).strip()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        current = next(
            (c for c in list_categories(conn) if c["id"] == category_id), None
        )
        if current is None:
            return "", 404
        try:
            rename_category(conn, current["name"], new_name)
        except ValueError as e:
            return _tbody(error=str(e), editing_id=category_id, status=422)
    return _tbody()


@bp.route("/categories/<int:category_id>/delete-btn")
def delete_btn(category_id):
    """Cancel the delete confirmation — restore the plain delete icon cell."""
    return render_template(
        "partials/editable_table_delete_icon.html",
        delete_confirm_url=url_for(
            "categories.delete_confirm", filename=g.filename, category_id=category_id
        ),
        edit_mode=True,
        reorderable=False,
    )


@bp.route("/categories/<int:category_id>/delete-confirm")
def delete_confirm(category_id):
    """Show the inline Yes/No delete confirmation for one row.

    Targets #categories-tbody (not the row) so a failed delete (category in use)
    surfaces its error row inside the tbody.
    """
    return render_template(
        "partials/editable_table_delete_confirm.html",
        delete_url=url_for(
            "categories.delete", filename=g.filename, category_id=category_id
        ),
        cancel_url=url_for(
            "categories.delete_btn", filename=g.filename, category_id=category_id
        ),
        delete_target="#categories-tbody",
        delete_swap="innerHTML",
    )


@bp.route("/categories/<int:category_id>/delete", methods=["POST"])
def delete(category_id):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        current = next(
            (c for c in list_categories(conn) if c["id"] == category_id), None
        )
        if current is None:
            return "", 404
        try:
            delete_category(conn, name=current["name"])
        except ValueError as e:
            return _tbody(error=str(e), status=422)
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp
