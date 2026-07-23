"""Category management — the dedicated "Categories" page.

Categories are a deliberately global table (not scoped to a snapshot, per
CLAUDE.md), but the page lives under the snapshot-scoped /s/<filename> prefix
purely for URL / navigation consistency (url_for needs `filename` to build
links back into the current snapshot, and the shared chrome + sidebar are
snapshot-scoped). None of the repository calls below use snapshot_id.

The page reuses the shared spreadsheet chrome (sheet.thead + editable-table +
.table-scroll-container) like Holdings/Budget: one "Category" column with
click-to-edit rename, an inline delete confirm in the actions column, and a
trailing add row. Every add / rename / delete re-renders the tbody
(#categories-tbody); an error (duplicate name, in-use delete) comes back as a
422 carrying the same tbody plus an inline error row (base.html's
htmx:beforeSwap hook swaps non-empty 422 bodies in).
"""

from flask import Blueprint, current_app, render_template, request

from web.routes.common import snapshot_scoped

from fintrack.ledger.repository.categories import (
    add_category,
    delete_category,
    list_categories,
    rename_category,
)

bp = snapshot_scoped(Blueprint("categories", __name__, url_prefix="/s/<filename>"))


def _tbody(error=None, editing_id=None, confirm_id=None, status=200):
    """Render the categories tbody rows (the swap target of every mutation)."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        cats = list_categories(conn)
    html = render_template(
        "partials/categories_tbody.html",
        cats=cats,
        error=error,
        editing_id=editing_id,
        confirm_id=confirm_id,
    )
    return html if status == 200 else (html, status)


@bp.route("/categories")
def index():
    """The Categories page (full chrome)."""
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        cats = list_categories(conn)
    return render_template("categories.html", active_tab="categories", cats=cats)


@bp.route("/categories/add", methods=["POST"])
def add():
    name = request.form.get("name", "").strip()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        try:
            add_category(conn, name=name)
        except ValueError as e:
            return _tbody(error=str(e), status=422)
    return _tbody()


@bp.route("/categories/<int:category_id>/edit")
def edit(category_id):
    """Switch one row into its inline rename editor."""
    return _tbody(editing_id=category_id)


@bp.route("/categories/<int:category_id>/row")
def row(category_id):
    """Revert a row back to display (cancel rename, e.g. on blur)."""
    return _tbody()


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
    """Cancel the delete confirmation, back to the plain delete icon."""
    return _tbody()


@bp.route("/categories/<int:category_id>/delete-confirm")
def delete_confirm(category_id):
    """Show the inline Yes/No delete confirmation for one row."""
    return _tbody(confirm_id=category_id)


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
    return _tbody()
