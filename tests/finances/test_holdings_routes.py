"""Tests for the holdings blueprint (web/routes/holdings.py): a read-only
unified view of accounts + asset_entries with liquidity-tier totals,
secured-pair equity, and filter query params.
"""

import pytest
from sqlalchemy import create_engine

from fintrack.core.db import init_db
from fintrack.accounts.repository import add_account
from fintrack.networth.repository import add_asset_entry
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with schema applied."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    """Flask test client with a 'finances' snapshot, a checking account, and
    a credit card, so the liquid tier total is checking - cc = 600."""
    from web.app import create_app

    with db_engine.connect() as conn:
        snap_id = create_snapshot(conn, "finances")
        add_account(
            conn,
            snap_id,
            {
                "name": "Checking",
                "type": "checking",
                "institution": "Chase",
                "balance": 1000,
            },
        )
        add_account(
            conn,
            snap_id,
            {
                "name": "Visa",
                "type": "credit_card",
                "institution": "Chase",
                "limit": 1000,
                "available": 600,
                "balance": -400,
            },
        )

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def test_holdings_view_returns_200_with_tier_labels(client):
    resp = client.get("/s/finances/holdings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Liquid" in body
    assert "Investable" in body
    assert "Net worth" in body


def test_holdings_view_shows_liquid_total(client):
    resp = client.get("/s/finances/holdings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # checking (+1000) + credit card (-400) = 600
    assert "600" in body


def test_holdings_view_tier_filter_returns_200(client):
    resp = client.get("/s/finances/holdings?tier=liquid")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Clear filters" in body


def test_holdings_view_liabilities_filter_returns_200(client):
    resp = client.get("/s/finances/holdings?kind=liabilities")
    assert resp.status_code == 200


def test_holdings_view_institution_filter_returns_200(client):
    resp = client.get("/s/finances/holdings?institution=Chase")
    assert resp.status_code == 200


def test_holdings_view_shows_equity_section(client, db_engine):
    with db_engine.connect() as conn:
        asset_id = add_asset_entry(
            conn,
            1,
            {"kind": "asset", "type": "real_estate", "name": "Home", "value": 400000},
        )
        add_asset_entry(
            conn,
            1,
            {
                "kind": "debt",
                "type": "loan",
                "name": "Mortgage",
                "balance": 300000,
                "assetRef": asset_id,
            },
        )

    resp = client.get("/s/finances/holdings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Equity" in body
    assert "Home" in body
    assert "Mortgage" in body
