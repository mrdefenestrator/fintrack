"""Timeline (#98): actual past month-ends and the projection in one window."""

from datetime import date
from decimal import Decimal

from fintrack.accounts.balance_history import month_end_points, record_balance
from fintrack.accounts.repository import add_account
from fintrack.networth.repository import add_asset_entry
from fintrack.projections import timeline

TODAY = date(2026, 7, 16)


def _checking(conn, snapshot_id):
    """Checking: $500 on Apr 10, $700 on May 20, $1,000 (current) on Jul 1."""
    acc = add_account(
        conn,
        snapshot_id,
        {
            "name": "Checking",
            "type": "checking",
            "balance": 500,
            "asOfDate": "2026-04-10",
        },
    )
    record_balance(conn, account_id=acc, balance=Decimal(700), as_of=date(2026, 5, 20))
    record_balance(conn, account_id=acc, balance=Decimal(1000), as_of=date(2026, 7, 1))
    return acc


def _row(tl, holding_id):
    return next(r for r in tl["rows"] if r["account"]["id"] == holding_id)


def test_month_end_points_carry_forward(conn, snapshot_id):
    acc = _checking(conn, snapshot_id)
    months = [(2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    cells = month_end_points(conn, [acc], months)[acc]
    assert cells[0] is None  # nothing recorded yet
    assert cells[1] == {"balance": Decimal(500), "as_of": date(2026, 4, 10)}
    assert cells[2]["balance"] == Decimal(700)
    # June has no point of its own: May's carries forward.
    assert cells[3] == {"balance": Decimal(700), "as_of": date(2026, 5, 20)}


def test_window_spans_past_and_future(conn, snapshot_id):
    acc = _checking(conn, snapshot_id)
    tl = timeline.build_timeline(
        conn, snapshot_id, start=(2026, 3), months=6, today=TODAY
    )
    assert [m["label"] for m in tl["months"]] == [
        "Mar 2026",
        "Apr 2026",
        "May 2026",
        "Jun 2026",
        "Jul 2026",
        "Aug 2026",
    ]
    assert [m["past"] for m in tl["months"]] == [True] * 4 + [False] * 2
    assert tl["now_pos"] == 4
    assert [m["index"] for m in tl["months"]] == [None] * 4 + [0, 1]

    cells = _row(tl, acc)["cells"]
    assert [c["value"] for c in cells] == [
        None,
        Decimal(500),
        Decimal(700),
        Decimal(700),
        Decimal(1000),  # projected (no flows)
        Decimal(1000),
    ]
    assert [c["carried"] for c in cells[:4]] == [False, False, False, True]
    assert cells[3]["as_of"] == date(2026, 5, 20)
    assert [c["future"] for c in cells] == [False] * 4 + [True] * 2

    # Totals: March is missing the one holding, so it's partial (and blank).
    assert tl["liquid"][0] == {"value": Decimal(0), "missing": 1}
    assert tl["liquid"][1] == {"value": Decimal(500), "missing": 0}
    assert tl["net_worth"][3] == {"value": Decimal(700), "missing": 0}
    assert tl["liquid"][4]["value"] == Decimal(1000)


def test_default_window_starts_now_and_matches_the_projection(conn, snapshot_id):
    _checking(conn, snapshot_id)
    tl = timeline.build_timeline(conn, snapshot_id, months=3, today=TODAY)
    assert tl["start"] == (2026, 7)
    assert not tl["has_past"]
    assert tl["now_pos"] == 0
    assert tl["summary"]["end_label"] == "Sep 2026"
    assert tl["summary"]["past"] is False
    # Opening = Now: the first month's change is measured from current balances.
    assert timeline.change_totals(tl)["liquid"][0] == {
        "value": Decimal(0),
        "missing": 0,
    }


def test_past_window_summary_is_actual(conn, snapshot_id):
    _checking(conn, snapshot_id)
    tl = timeline.build_timeline(
        conn, snapshot_id, start=(2026, 4), months=2, today=TODAY
    )
    assert not tl["has_future"]
    assert tl["now_pos"] is None
    s = tl["summary"]
    assert s["past"] is True and s["end_label"] == "May 2026"
    assert s["liquid"] == {
        "end": Decimal(700),
        "change": Decimal(300),
        "partial": False,
    }


def test_assets_and_loans_count_toward_past_net_worth(conn, snapshot_id):
    _checking(conn, snapshot_id)
    add_asset_entry(
        conn,
        snapshot_id,
        {"kind": "asset", "name": "Car", "value": 9000, "asOfDate": "2026-05-01"},
    )
    add_asset_entry(
        conn,
        snapshot_id,
        {
            "kind": "debt",
            "name": "Car loan",
            "balance": 4000,
            "asOfDate": "2026-05-01",
        },
    )
    tl = timeline.build_timeline(
        conn, snapshot_id, start=(2026, 4), months=3, today=TODAY
    )
    # April: only checking existed → partial; May on: all three.
    assert tl["net_worth"][0] == {"value": Decimal(500), "missing": 2}
    assert tl["liquid"][0]["missing"] == 0  # assets/loans aren't liquid
    assert tl["net_worth"][1] == {
        "value": Decimal(700 + 9000 - 4000),
        "missing": 0,
    }


def test_changes_measure_current_month_from_now():
    months = [
        {"current": False},
        {"current": False},
        {"current": True},
        {"current": False},
    ]
    out = timeline.changes(
        [None, Decimal(10), Decimal(30), Decimal(35)], Decimal(5), months, Decimal(25)
    )
    # No base for a missing value; the current month counts from Now (25).
    assert out == [None, None, Decimal(5), Decimal(5)]
    out = timeline.changes([Decimal(10), Decimal(12)], Decimal(5), months[:2], 0)
    assert out == [Decimal(5), Decimal(2)]


def test_paging_bounds(conn, snapshot_id):
    _checking(conn, snapshot_id)  # earliest point: Apr 2026
    tl = timeline.build_timeline(conn, snapshot_id, months=6, today=TODAY)
    assert tl["can_go_back"] and tl["can_go_forward"]
    assert tl["prev_start"] == (2026, 1)
    assert tl["next_start"] == (2027, 1)
    early = timeline.build_timeline(
        conn, snapshot_id, start=(2026, 4), months=6, today=TODAY
    )
    assert not early["can_go_back"]
    # A window past the 60-month horizon is pulled back to end at its edge.
    far = timeline.build_timeline(
        conn, snapshot_id, start=(2035, 1), months=12, today=TODAY
    )
    assert far["end"] == (2031, 6)
    assert not far["can_go_forward"]


def test_parse_month():
    assert timeline.parse_month("2026-01") == (2026, 1)
    assert timeline.parse_month("2026-13") is None
    assert timeline.parse_month("nope") is None
    assert timeline.parse_month(None) is None


def test_change_totals_are_like_for_like(conn, snapshot_id):
    """A past total's change sums the holdings with a value in both months
    and says how many it left out, instead of going blank."""
    _checking(conn, snapshot_id)
    add_asset_entry(
        conn,
        snapshot_id,
        {"kind": "asset", "name": "Car", "value": 9000, "asOfDate": "2026-05-01"},
    )
    tl = timeline.build_timeline(
        conn, snapshot_id, start=(2026, 4), months=4, today=TODAY
    )
    totals = timeline.change_totals(tl)
    # April: checking had no March value; the car none at all.
    assert totals["net_worth"][0] == {"value": None, "missing": 0}
    # May: checking +200; the car has no April value to compare.
    assert totals["net_worth"][1] == {"value": Decimal(200), "missing": 1}
    assert totals["liquid"][1] == {"value": Decimal(200), "missing": 0}
    # July (current) is measured from Now and equals the projection's change.
    proj = tl["projection"]
    assert totals["net_worth"][3]["value"] == (
        proj["net_worth"][0] - proj["start_net_worth"]
    )
