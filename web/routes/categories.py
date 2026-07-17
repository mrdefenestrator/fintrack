"""Category management panel routes ("Manage categories" on the Merchants
page).

Categories are a deliberately global table (not scoped to a snapshot, per
CLAUDE.md), but the panel lives on the snapshot-scoped Merchants page, so
this blueprint mounts at the same /s/<filename> prefix purely for URL /
navigation consistency (url_for needs `filename` to build links back into
the current snapshot). None of the repository calls below use snapshot_id.
"""

from flask import Blueprint, current_app, make_response, render_template, request

from web.routes.common import snapshot_scoped

from fintrack.ledger.repository.categories import (
    add_category,
    delete_category,
    list_categories,
    rename_category,
)

bp = snapshot_scoped(Blueprint("categories", __name__, url_prefix="/s/<filename>"))


def _panel(error=None, editing_id=None, confirm_id=None):
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        cats = list_categories(conn)
    return render_template(
        "partials/categories_panel_body.html",
        cats=cats,
        error=error,
        editing_id=editing_id,
        confirm_id=confirm_id,
    )


@bp.route("/categories/panel")
def panel():
    """Full re-render of the panel body (e.g. after an outer nav refresh)."""
    return _panel()


def _refreshed(html: str):
    """A successful mutation response: the app's other selects/rows that read
    the category list (merchants filter, transaction/merchant category
    dropdowns) aren't re-rendered in place, so mirror the existing
    accounts/budget "move" convention (web/routes/crud.py handle_move) of
    asking htmx to do a full page refresh rather than trying to patch every
    consumer individually."""
    resp = make_response(html)
    resp.headers["HX-Refresh"] = "true"
    return resp


@bp.route("/categories/add", methods=["POST"])
def add():
    name = request.form.get("name", "").strip()
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        try:
            add_category(conn, name=name)
        except ValueError as e:
            return _panel(error=str(e)), 422
    return _refreshed(_panel())


@bp.route("/categories/<int:category_id>/edit")
def edit(category_id):
    """Switch one row into its inline rename editor."""
    return _panel(editing_id=category_id)


@bp.route("/categories/<int:category_id>/row")
def row(category_id):
    """Revert a row back to display (cancel rename, e.g. on blur)."""
    return _panel()


@bp.route("/categories/<int:category_id>/rename", methods=["POST"])
def rename(category_id):
    # `value` is the spreadsheet cell input name (ledger_cells.html); `name`
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
            return _panel(error=str(e), editing_id=category_id), 422
    return _refreshed(_panel())


@bp.route("/categories/<int:category_id>/delete-btn")
def delete_btn(category_id):
    """Cancel the delete confirmation, back to the plain delete icon."""
    return _panel()


@bp.route("/categories/<int:category_id>/delete-confirm")
def delete_confirm(category_id):
    """Show the inline Yes/No delete confirmation for one row."""
    return _panel(confirm_id=category_id)


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
            return _panel(error=str(e)), 422
    return _refreshed(_panel())
