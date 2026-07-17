"""Status blueprint — snapshot picker at root; legacy status URL redirects."""

from flask import Blueprint, current_app, redirect, render_template, url_for

from fintrack.snapshots.repository import list_snapshots

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
    """Legacy status-page URL. The Status page was removed (QA issue 6);
    redirect old bookmarks to the Accounts view."""
    return redirect(url_for("accounts.accounts_view", filename=filename))
