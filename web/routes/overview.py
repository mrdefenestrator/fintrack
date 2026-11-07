"""Overview: the snapshot landing page (`/s/<snapshot>/`).

A stack of read-only cards — a headline number, a line or two of context,
and a link to the view behind it. Not a spreadsheet: the sheets stay dense
tables and this page holds the summary information. The numbers come from
fintrack.insights.overview (shared with the `status` CLI command).
"""

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.insights.overview import build_overview
from web.routes.common import get_common_context, validate_snapshot

overview_bp = Blueprint("overview", __name__)


@overview_bp.route("/s/<string:filename>/")
def overview_view(filename):
    snapshot_id = validate_snapshot(filename)
    # Nothing here is editable, but carry edit mode through so the nav and
    # header keep it (as Projections does).
    edit_mode = request.args.get("edit") == "1"
    context = get_common_context(snapshot_id, filename, edit_mode)
    with current_app.config["engine"].connect() as conn:
        overview = build_overview(conn, snapshot_id)

    def proj_url(month=None):
        """Projections on the Overview's horizon, optionally opened on a month."""
        return url_for(
            "projections.projections_view",
            filename=filename,
            months=overview["months"],
            month=month,
        )

    return render_template(
        "overview.html",
        active_tab="overview",
        overview=overview,
        summary=overview["summary"],
        proj_url=proj_url,
        **context,
    )
