"""Overview: the snapshot landing page (`/s/<snapshot>`).

A stack of read-only cards — a headline number, a line or two of context,
and a link to the view behind it. Not a spreadsheet: the sheets stay dense
tables and this page holds the summary information. The numbers come from
fintrack.insights.overview (shared with the `status` CLI command).
"""

from flask import Blueprint, current_app, render_template, request, url_for

from fintrack.insights.overview import LOOKBACK_MONTHS, build_overview
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
        past=overview["past"],
        # Net worth / Liquid: Projections' timeline over the same past year
        # and next (plain Projections when there's no history back then).
        timeline_url=_timeline_url(filename, overview),
        # Projection warnings: Projections on the Overview's horizon, opened on
        # the low point's month breakdown.
        warnings_url=url_for(
            "projections.projections_view",
            filename=filename,
            months=overview["months"],
            month=overview["summary"]["lowest_liquid"]["month_key"],
        ),
        # Cash flow card: Trends' Trailing 12, the window its average covers.
        trends_url=url_for("trends.index", filename=filename, period="trailing12"),
        # Spending alerts card: every flagged transaction, any month.
        flagged_url=url_for(
            "transactions.index",
            filename=filename,
            all_months="true",
            status="flagged",
        ),
        # Debt and cards: Holdings at the Loans band (Credit Cards without loans).
        debt_url=url_for("holdings.holdings_view", filename=filename)
        + ("#holdings-loan" if overview["debt_cards"]["loans"] else "#holdings-credit"),
        # Needs attention: one link, following what the card leads with —
        # Holdings for stale data, else Budget's Recurring view
        # filtered to the recurring-charge findings.
        attention_url=_attention_url(filename, overview["attention"]),
        **context,
    )


def _timeline_url(filename: str, overview: dict) -> str:
    past = overview["past"]
    if past["liquid"] is None and past["net_worth"] is None:
        return url_for(
            "projections.projections_view",
            filename=filename,
            months=overview["months"],
        )
    return url_for(
        "projections.projections_view",
        filename=filename,
        months=LOOKBACK_MONTHS + overview["months"],
        **{"from": past["from"]},
    )


def _attention_url(filename: str, items: list[dict]) -> str:
    if items and items[0]["source"] == "stale":
        return url_for("holdings.holdings_view", filename=filename)
    return url_for(
        "budget.budget_view",
        filename=filename,
        view="recurring",
        status=["not_budgeted", "price_change"],
    )
