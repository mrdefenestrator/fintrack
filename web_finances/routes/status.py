"""Status blueprint - shows snapshot selection at root."""

from flask import Blueprint, current_app, render_template

from finances.repository.snapshots import list_snapshots

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
