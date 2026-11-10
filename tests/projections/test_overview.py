"""build_overview: the numbers behind the Overview page and `status`."""

from fintrack.insights.overview import OVERVIEW_MONTHS, build_overview
from fintrack.projections.engine import project, summarize
from tests.projections.test_summary import TODAY, _household


def test_overview_reports_the_12_month_projection(conn, snapshot_id):
    _household(conn, snapshot_id)
    overview = build_overview(conn, snapshot_id, today=TODAY)
    assert overview["months"] == OVERVIEW_MONTHS == 12
    assert overview["cards"] == [
        "net_worth_liquid",
        "cash_flow",
        "spending_alerts",
        "projection_warnings",
    ]
    assert overview["has_holdings"] is True
    expected = summarize(project(conn, snapshot_id, months=12, today=TODAY))
    assert overview["summary"] == expected


def test_overview_of_an_empty_snapshot(conn, snapshot_id):
    overview = build_overview(conn, snapshot_id, today=TODAY)
    assert overview["has_holdings"] is False
    summary = overview["summary"]
    assert summary["net_worth"]["start"] == 0
    assert summary["warnings"] == []
    assert summary["payoffs"] == []
