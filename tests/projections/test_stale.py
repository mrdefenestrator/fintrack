"""Stale data (issue #102): stale balances and overdue statement imports."""

from datetime import UTC, date, datetime
from types import MappingProxyType

import pytest
from sqlalchemy import update

from fintrack.accounts.repository import add_account
from fintrack.core.models import imports
from fintrack.insights import stale
from fintrack.insights.overview import needs_attention
from fintrack.insights.settings import InsightsConfig, load_insights_config
from fintrack.ledger.repository import confirm_import, create_import
from fintrack.networth.repository import add_asset_entry

TODAY = date(2026, 11, 10)
CONFIG = InsightsConfig()


def _account(conn, snapshot_id, name, as_of, type_="checking"):
    return add_account(
        conn,
        snapshot_id,
        {"name": name, "type": type_, "balance": 100, "asOfDate": as_of},
    )


def _import(conn, account_id, on: date, status="confirmed"):
    imp = create_import(
        conn, account_id=account_id, filename="s.ofx", file_hash=f"h{on}"
    )
    conn.execute(
        update(imports)
        .where(imports.c.id == imp)
        .values(imported_at=datetime(on.year, on.month, on.day, 12, tzinfo=UTC))
    )
    if status == "confirmed":
        confirm_import(conn, imp)


@pytest.mark.parametrize(
    ("as_of", "group", "expected"),
    [
        ("2026-10-06", "cash", None),  # 35 days: not yet
        ("2026-10-05", "cash", "stale"),  # 36 days
        ("2026-08-06", "cash", "very_stale"),  # 96 days
        ("2026-10-05", "asset", None),  # assets get 90 days
        ("2026-08-11", "asset", "stale"),  # 91 days
        ("2026-05-01", "loan", "very_stale"),
        (None, "cash", None),
        ("garbage", "cash", None),
    ],
)
def test_staleness_per_group(as_of, group, expected):
    assert stale.staleness(as_of, group, TODAY, CONFIG) == expected


def test_find_stale_balances_oldest_first(conn, snapshot_id):
    _account(conn, snapshot_id, "Fresh Checking", "2026-11-01")
    _account(conn, snapshot_id, "Old Savings", "2026-10-01", type_="savings")
    _account(conn, snapshot_id, "Older Card", "2026-09-01", type_="credit_card")
    add_asset_entry(
        conn,
        snapshot_id,
        {
            "name": "House",
            "kind": "asset",
            "type": "real_estate",
            "value": 1,
            "asOfDate": "2026-09-01",
        },
    )
    items = stale.find_stale(conn, snapshot_id, TODAY, CONFIG)
    assert [(i["name"], i["days"]) for i in items] == [
        ("Older Card", 70),
        ("Old Savings", 40),
    ]
    assert stale.summary_line(items[0]) == "Older Card: last updated 70 days ago"


def test_import_reminder_only_for_accounts_that_import(conn, snapshot_id):
    overdue = _account(conn, snapshot_id, "Chase Checking", "2026-11-09")
    recent = _account(conn, snapshot_id, "Ally Savings", "2026-11-09")
    _account(conn, snapshot_id, "Manual Cash", "2026-11-09")  # never imports
    _import(conn, overdue, date(2026, 9, 20))
    _import(conn, recent, date(2026, 9, 1))
    _import(conn, recent, date(2026, 11, 1))
    _import(conn, recent, date(2026, 11, 8), status="staging")  # not counted
    items = stale.find_stale(conn, snapshot_id, TODAY, CONFIG)
    assert [(i["kind"], i["name"], i["days"]) for i in items] == [
        ("import", "Chase Checking", 51)
    ]
    assert (
        stale.summary_line(items[0])
        == "Chase Checking: no statement imported in 51 days"
    )


def test_stale_balance_wins_over_import_reminder(conn, snapshot_id):
    acct = _account(conn, snapshot_id, "Chase Checking", "2026-09-01")
    _import(conn, acct, date(2026, 9, 1))
    # Confirming records the import's balance date; keep As Of stale.
    items = stale.find_stale(conn, snapshot_id, TODAY, CONFIG)
    assert [i["kind"] for i in items] == ["balance"]


def test_stale_items_reach_needs_attention(conn, snapshot_id):
    _account(conn, snapshot_id, "Old Savings", "2026-10-01", type_="savings")
    assert needs_attention(conn, snapshot_id, today=TODAY) == [
        {"source": "stale", "text": "Old Savings: last updated 40 days ago"}
    ]


def test_config_defaults_match_the_shipped_file():
    assert load_insights_config() == InsightsConfig()


def test_config_merges_partial_groups(tmp_path):
    path = tmp_path / "insights.yaml"
    path.write_text("stale_days: {cash: 20}\nimport_stale_days: 50\n")
    cfg = load_insights_config(path)
    assert cfg.stale_days == MappingProxyType(
        {"cash": 20, "credit_card": 35, "loan": 90, "asset": 90}
    )
    assert cfg.import_stale_days == 50
    assert load_insights_config(tmp_path / "missing.yaml") == InsightsConfig()


@pytest.mark.parametrize(
    "text", ["stail_days: {cash: 1}\n", "stale_days: {checking: 1}\n"]
)
def test_config_rejects_typos(tmp_path, text):
    path = tmp_path / "insights.yaml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_insights_config(path)
