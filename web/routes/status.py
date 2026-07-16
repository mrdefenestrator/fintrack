"""Status blueprint — snapshot picker at root, status dashboard per snapshot."""

from flask import Blueprint, current_app, render_template, request

from fintrack.snapshots.repository import list_snapshots
from web.routes.common import get_common_context, validate_snapshot

status_bp = Blueprint("status", __name__)


@status_bp.route("/")
def status_view():
    engine = current_app.config["engine"]
    with engine.connect() as conn:
        available_files = list_snapshots(conn)
    return render_template(
        "file_select.html",
        filename="",
        available_files=available_files,
        edit_mode=False,
        n2=None,
        n3=None,
        n6=None,
    )


@status_bp.route("/s/<string:filename>/status")
def snapshot_status(filename):
    """Key-numbers rollup for one snapshot (mirrors the CLI status command)."""
    snapshot_id = validate_snapshot(filename)
    edit_mode = request.args.get("edit") == "1"
    context = get_common_context(snapshot_id, filename, edit_mode)
    total = context["n2"] + context["n3"] + context["n6"]
    status_rows = [
        ("Accounts", context["n2"]),
        ("Budget (prorated)", context["n3"]),
        ("Assets", context["n6"]),
        ("Total", total),
    ]
    return render_template(
        "status.html",
        active_tab="status",
        status_rows=status_rows,
        **context,
    )
