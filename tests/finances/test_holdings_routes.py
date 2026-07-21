"""Tests for the holdings blueprint (web/routes/holdings.py): a read-only
unified view of accounts + asset_entries, reusing the shared sheet chrome
(filter bar, total row) with liquidity-tier columns, a bottom net-worth total,
secured-pair equity folded onto the loan row, and multi-select filters.
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
    a credit card, so the net-worth total is checking - cc = 600."""
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


def test_holdings_view_returns_200_with_columns(client):
    resp = client.get("/s/finances/holdings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Sheet column headers (not a bespoke top panel).
    for header in (
        "Institution",
        "Name",
        "Type",
        "Unit Price",
        "Qty",
        "Amount",
        "Equity",
        "LTV",
    ):
        assert header in body
    # Both holdings render.
    assert "Checking" in body
    assert "Visa" in body


def test_holdings_view_has_no_tier_or_kind_surface(client):
    """Tier and Kind are both derivable from type, so neither appears as a
    column header or a filter."""
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    rows = _rows_region(body)
    assert ">Tier<" not in body
    assert ">Kind<" not in body
    assert "Semi-liquid" not in rows
    assert 'name="tier"' not in body


def test_holdings_view_row_accents_by_asset_liability(client):
    """Asset rows get the green left accent, liability rows the red one."""
    resp = client.get("/s/finances/holdings")
    rows = _rows_region(resp.get_data(as_text=True))
    assert "border-l-emerald-400" in rows  # the checking account (asset)
    assert "border-l-rose-400" in rows  # the credit card (liability)


def test_holdings_view_shows_bottom_total(client):
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    # Pinned total row (class picked up by sortable.js) summing displayed rows:
    # checking (+1000) + credit card (-400) = 600.
    assert "total-row" in body
    assert "600" in body


def test_holdings_view_reuses_shared_filter_bar(client):
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    # Shared filter-bar chrome + multi-select dropdowns, not bespoke pills.
    assert "holdings-filter-form" in body
    assert "filter-dropdown-trigger" in body
    assert "Reset" in body


def test_holdings_view_type_filter(client):
    # The Type filter offers the present types and narrows to the match.
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    assert 'name="type"' in body  # Type filter control present
    assert 'value="credit_card"' in body  # a present type is offered

    resp = client.get("/s/finances/holdings?type=credit_card")
    assert resp.status_code == 200
    rows = _rows_region(resp.get_data(as_text=True))
    assert "Visa" in rows
    assert "Checking" not in rows


def test_holdings_view_balance_filter_liabilities(client):
    # balance=liability keeps only negative-contribution holdings (the card).
    resp = client.get("/s/finances/holdings?balance=liability")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Visa" in body
    assert "Checking" not in _rows_region(body)


def test_holdings_view_institution_filter_returns_200(client):
    resp = client.get("/s/finances/holdings?institution=Chase")
    assert resp.status_code == 200


def test_holdings_view_folds_equity_onto_loan_row(client, db_engine):
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
    assert "Home" in body
    assert "Mortgage" in body
    # Equity (400000 - 300000) and LTV (300000/400000 = 75.0%) fold into the row.
    assert "100,000" in body
    assert "75.0%" in body


def _rows_region(body: str) -> str:
    """The <tbody> slice, so header/filter labels don't cause false matches."""
    start = body.find("<tbody")
    end = body.find("</tbody>")
    return body[start:end] if start != -1 and end != -1 else body


# ---------------------------------------------------------------------------
# Inline editing (slice 1: Type / Name / Institution / Unit), dispatched to the
# right repository by row source.
# ---------------------------------------------------------------------------


def _account_by_name(db_engine, name):
    from fintrack.accounts.repository import get_accounts

    with db_engine.connect() as conn:
        return next(a for a in get_accounts(conn, 1) if a["name"] == name)


def test_holdings_edit_mode_makes_cells_clickable(client):
    resp = client.get("/s/finances/holdings?edit=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Editable cells become hx-get links to the holdings cell_edit route.
    assert "/holdings/cell/account/" in body


def test_holdings_cell_edit_returns_type_select(client, db_engine):
    acc = _account_by_name(db_engine, "Checking")
    resp = client.get(f"/s/finances/holdings/cell/account/{acc['id']}?field=type")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "<select" in body


def test_holdings_update_account_type_dispatches_to_accounts(client, db_engine):
    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "type", "value": "savings"},
    )
    assert resp.status_code == 200
    assert _account_by_name(db_engine, "Checking")["type"] == "savings"


def test_holdings_update_asset_type_and_unit_dispatch_to_assets(client, db_engine):
    from fintrack.networth.repository import add_asset_entry, get_asset_entries

    with db_engine.connect() as conn:
        add_asset_entry(conn, 1, {"kind": "asset", "name": "Coins", "value": 100})

    # First (index 0) asset entry.
    r1 = client.post(
        "/s/finances/holdings/update/asset/0",
        data={"field": "type", "value": "digital_wallet"},
    )
    r2 = client.post(
        "/s/finances/holdings/update/asset/0",
        data={"field": "unit", "value": "btc"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    with db_engine.connect() as conn:
        entry = get_asset_entries(conn, 1)[0]
    assert entry["type"] == "digital_wallet"
    assert entry["unit"] == "BTC"  # normalized to upper-case


def test_holdings_update_empty_name_is_422(client, db_engine):
    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "name", "value": ""},
    )
    assert resp.status_code == 422


def test_holdings_update_rejects_non_editable_field(client, db_engine):
    acc = _account_by_name(db_engine, "Checking")
    # `balance` is not editable in slice 1 (identity/classification only).
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "balance", "value": "999"},
    )
    assert resp.status_code == 422
