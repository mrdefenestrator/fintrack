"""Overview: the snapshot landing page (`/s/<snapshot>`).

A stack of read-only cards — a headline number, a line or two of context,
and a link to the view behind it. Not a spreadsheet: the sheets stay dense
tables and this page holds the summary information. The numbers come from
fintrack.insights.overview (shared with the `status` CLI command).
"""

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.insights.overview import build_overview
from web.routes.common import get_common_context, validate_snapshot

overview_bp = Blueprint("overview", __name__)


# No trailing slash, and strict_slashes off so `/s/<snapshot>/` matches too
# without a redirect: the PR-preview Lambda strips the trailing slash, so a
# slash-redirect there loops forever.
@overview_bp.route("/s/<string:filename>", strict_slashes=False)
def overview_view(filename):
    snapshot_id = validate_snapshot(filename)
    # Nothing here is editable, but carry edit mode through so the nav and
    # header keep it (as Projections does).
    edit_mode = request.args.get("edit") == "1"
    context = get_common_context(snapshot_id, filename, edit_mode)
    with current_app.config["engine"].connect() as conn:
        overview = build_overview(conn, snapshot_id)

    return render_template(
        "overview.html",
        active_tab="overview",
        overview=overview,
        summary=overview["summary"],
        # Projections on the Overview's horizon.
        proj_url=url_for(
            "projections.projections_view",
            filename=filename,
            months=overview["months"],
        ),
        # Cash flow card: Trends' Trailing 12, the window its average covers.
        trends_url=url_for("trends.index", filename=filename, period="trailing12"),
        # Needs attention card: every flagged transaction, any month.
        flagged_url=url_for(
            "transactions.index",
            filename=filename,
            all_months="true",
            status="flagged",
        ),
        **context,
    )
