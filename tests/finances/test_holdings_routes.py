"""Tests for the holdings blueprint (web/routes/holdings.py): a read-only
unified view of accounts + asset_entries, reusing the shared sheet chrome
(filter bar, total row) with liquidity-tier columns, a bottom net-worth total,
secured-pair equity folded onto the loan row, and multi-select filters.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

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
        "Original",
        "Term",
        "Originated",
        "P&amp;I",
        "Paid",
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
    """Rows carry a data-accent recording asset/liability polarity. (The left
    accent rail is now colored per group, not by this attribute — issue #4 — but
    the attribute is kept for semantics.)"""
    resp = client.get("/s/finances/holdings")
    rows = _rows_region(resp.get_data(as_text=True))
    assert 'data-accent="asset"' in rows  # the checking account
    assert 'data-accent="liability"' in rows  # the credit card


def test_holdings_view_shows_bottom_total(client):
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    # Pinned master total row summing every group: checking (+1000) + card
    # (-400) = 600 net worth.
    assert "total-row" in body
    assert "600" in body


def test_holdings_grouped_layout(client):
    """One table, four type-based groups (Cash, Credit Cards, Loans, Assets),
    each with its own heading + header row; a master footer closes with
    Liquid + Net worth."""
    resp = client.get("/s/finances/holdings")
    body = resp.get_data(as_text=True)
    assert "holdings-group-heading" in body
    # Every group heading renders (empty groups included).
    for label in ("Cash", "Credit Cards", "Loans", "Assets"):
        assert label in body
    assert "Liquid" in body
    assert "Net worth" in body
    # Group-specific columns appear, proving per-group headers.
    assert "Unit Price" in body  # Assets
    assert "Equity" in body  # Loans
    # Each group has its own column headers as real <th> cells.
    assert 'data-group="cash"' in body
    assert 'data-sort-key="c4"' in body


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
    """The holdings <table> content only, so filter-bar option labels above the
    table (e.g. a Type option named "Checking") don't cause false matches."""
    start = body.find('id="holdings-table"')
    end = body.find("</table>", start)
    return body[start:end] if start != -1 and end != -1 else body


# ---------------------------------------------------------------------------
# Inline editing (slice 1: Type / Name / Institution / Unit), dispatched to the
# right repository by row source.
# ---------------------------------------------------------------------------


def _account_by_name(db_engine, name):
    from fintrack.accounts.repository import get_accounts

    with db_engine.connect() as conn:
        return next(a for a in get_accounts(conn, 1) if a["name"] == name)


def _asset_by_name(db_engine, name):
    from fintrack.networth.repository import get_asset_entries

    with db_engine.connect() as conn:
        return next(e for e in get_asset_entries(conn, 1) if e["name"] == name)


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
        aid = add_asset_entry(conn, 1, {"kind": "asset", "name": "Coins", "value": 100})

    r1 = client.post(
        f"/s/finances/holdings/update/asset/{aid}",
        data={"field": "type", "value": "digital_wallet"},
    )
    r2 = client.post(
        f"/s/finances/holdings/update/asset/{aid}",
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
    # `limit` is credit-card-only, so it is not editable on a checking account.
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "limit", "value": "999"},
    )
    assert resp.status_code == 422


def test_holdings_update_cash_balance(client, db_engine):
    from fintrack.accounts.repository import get_accounts

    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "balance", "value": "2500"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        updated = next(a for a in get_accounts(conn, 1) if a["id"] == acc["id"])
    assert updated["balance"] == 2500


def test_holdings_update_balance_stamps_client_date(client, db_engine):
    """A balance edit dates its balance-history point from the browser's
    X-Local-Date header (QA #6), not the server's timezone."""
    from datetime import date

    from fintrack.accounts.balance_history import get_balance_history

    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "balance", "value": "2600"},
        headers={"X-Local-Date": "2026-03-04"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        points = get_balance_history(conn, acc["id"])
    stamped = [p for p in points if p["balance"] == 2600]
    assert stamped and stamped[-1]["as_of"] == date(2026, 3, 4)


def test_holdings_update_cc_limit_editable(client, db_engine):
    from fintrack.accounts.repository import get_accounts

    cc = _account_by_name(db_engine, "Visa")
    resp = client.post(
        f"/s/finances/holdings/update/account/{cc['id']}",
        data={"field": "limit", "value": "1500"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        updated = next(a for a in get_accounts(conn, 1) if a["id"] == cc["id"])
    assert updated["limit"] == 1500


def test_holdings_update_cc_balance_editable(client, db_engine):
    # QA #10: the credit-card balance (Amount) is the editable input; available
    # is the computed column, recomputed as credit_limit + balance.
    from fintrack.accounts.repository import get_accounts

    cc = _account_by_name(db_engine, "Visa")
    resp = client.post(
        f"/s/finances/holdings/update/account/{cc['id']}",
        data={"field": "balance", "value": "-200"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        updated = next(a for a in get_accounts(conn, 1) if a["id"] == cc["id"])
    assert updated["balance"] == -200
    assert updated["available"] == updated["limit"] + updated["balance"]


def test_holdings_update_cc_available_not_editable(client, db_engine):
    # QA #10: Available is computed (credit_limit + balance), so it is read-only.
    cc = _account_by_name(db_engine, "Visa")
    resp = client.post(
        f"/s/finances/holdings/update/account/{cc['id']}",
        data={"field": "available", "value": "500"},
    )
    assert resp.status_code == 422


def test_holdings_update_asset_value_and_qty(client, db_engine):
    from fintrack.networth.repository import add_asset_entry, get_asset_entries

    with db_engine.connect() as conn:
        aid = add_asset_entry(
            conn, 1, {"kind": "asset", "name": "Home", "value": 400000}
        )

    r1 = client.post(
        f"/s/finances/holdings/update/asset/{aid}",
        data={"field": "value", "value": "450000"},
    )
    r2 = client.post(
        f"/s/finances/holdings/update/asset/{aid}",
        data={"field": "quantity", "value": "2"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    with db_engine.connect() as conn:
        entry = get_asset_entries(conn, 1)[0]
    assert entry["value"] == 450000
    assert entry["quantity"] == 2


def test_holdings_update_debt_interest(client, db_engine):
    from fintrack.networth.repository import add_asset_entry, get_asset_entries

    with db_engine.connect() as conn:
        add_asset_entry(conn, 1, {"kind": "debt", "name": "Loan", "balance": 1000})
    loan_id = _asset_by_name(db_engine, "Loan")["id"]

    resp = client.post(
        f"/s/finances/holdings/update/asset/{loan_id}",
        data={"field": "interestRate", "value": "0.055"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        entry = get_asset_entries(conn, 1)[0]
    assert float(entry["interestRate"]) == 0.055


def test_holdings_loan_origination_fields_and_derived_values(client, db_engine):
    from fintrack.networth.repository import get_asset_entries

    with db_engine.connect() as conn:
        add_asset_entry(
            conn,
            1,
            {
                "kind": "debt",
                "type": "loan",
                "name": "Mortgage",
                "balance": 240000,
                "interestRate": 0.06,
                "originalPrincipal": 300000,
                "termMonths": 360,
                "originationDate": "2020-01-10",
                "statement_due_day_of_month": 31,
            },
        )

    body = client.get("/s/finances/holdings?edit=1").get_data(as_text=True)
    assert "$300,000.00" in body
    assert "360 mo" in body
    assert "2020-01-10" in body
    assert "$1,798.65" in body
    assert "20.0%" in body
    assert "31st" in body

    mortgage_id = _asset_by_name(db_engine, "Mortgage")["id"]
    for field, value in (
        ("originalPrincipal", "310000"),
        ("termMonths", "180"),
        ("originationDate", "2021-02-03"),
        ("statement_due_day_of_month", "15"),
    ):
        resp = client.post(
            f"/s/finances/holdings/update/asset/{mortgage_id}",
            data={"field": field, "value": value},
        )
        assert resp.status_code == 200

    with db_engine.connect() as conn:
        loan = get_asset_entries(conn, 1)[0]
    assert loan["originalPrincipal"] == 310000
    assert loan["termMonths"] == 180
    assert loan["originationDate"] == "2021-02-03"
    assert loan["statement_due_day_of_month"] == 15


def test_holdings_rejects_invalid_loan_due_day(client, db_engine):
    with db_engine.connect() as conn:
        add_asset_entry(
            conn,
            1,
            {"kind": "debt", "type": "loan", "name": "Loan", "balance": 1000},
        )
    loan_id = _asset_by_name(db_engine, "Loan")["id"]
    resp = client.post(
        f"/s/finances/holdings/update/asset/{loan_id}",
        data={"field": "statement_due_day_of_month", "value": "32"},
    )
    assert resp.status_code == 422


def test_holdings_update_cc_payment_account_ref(client, db_engine):
    from fintrack.accounts.repository import get_accounts

    checking = _account_by_name(db_engine, "Checking")
    cc = _account_by_name(db_engine, "Visa")
    # Point the card's payment account at Checking (Linked ref picker).
    resp = client.post(
        f"/s/finances/holdings/update/account/{cc['id']}",
        data={"field": "paymentAccountRef", "value": str(checking["id"])},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        updated = next(a for a in get_accounts(conn, 1) if a["id"] == cc["id"])
    assert updated["paymentAccountRef"] == checking["id"]


def test_holdings_update_debt_asset_ref(client, db_engine):
    from fintrack.networth.repository import add_asset_entry, get_asset_entries

    with db_engine.connect() as conn:
        asset_id = add_asset_entry(
            conn,
            1,
            {"kind": "asset", "type": "real_estate", "name": "Home", "value": 1},
        )
        add_asset_entry(conn, 1, {"kind": "debt", "name": "Mortgage", "balance": 1})

    mortgage_id = _asset_by_name(db_engine, "Mortgage")["id"]
    resp = client.post(
        f"/s/finances/holdings/update/asset/{mortgage_id}",
        data={"field": "assetRef", "value": str(asset_id)},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        debt = get_asset_entries(conn, 1)[1]
    assert debt["assetRef"] == asset_id


def test_holdings_update_invalid_number_is_422(client, db_engine):
    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(
        f"/s/finances/holdings/update/account/{acc['id']}",
        data={"field": "balance", "value": "not-a-number"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Delete (sticky actions column), dispatched by row source.
# ---------------------------------------------------------------------------


def test_holdings_edit_mode_shows_delete_actions(client):
    resp = client.get("/s/finances/holdings?edit=1")
    body = resp.get_data(as_text=True)
    assert "table-actions-cell" in body
    assert "/holdings/delete-confirm/account/" in body


def test_holdings_delete_account_dispatches(client, db_engine):
    from fintrack.accounts.repository import get_accounts

    acc = _account_by_name(db_engine, "Checking")
    resp = client.post(f"/s/finances/holdings/delete/account/{acc['id']}")
    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    with db_engine.connect() as conn:
        names = [a["name"] for a in get_accounts(conn, 1)]
    assert "Checking" not in names


def test_holdings_reorder_accounts_dispatches(client, db_engine):
    from fintrack.accounts.repository import add_account, get_accounts

    # Add a second cash account so the Cash group has two rows to reorder. The
    # Visa credit card is a separate band, so it stays put while the two cash
    # rows swap; get_accounts lists the cash band before the credit band.
    with db_engine.connect() as conn:
        add_account(conn, 1, {"name": "Savings", "type": "savings", "balance": 50})
    # Reverse the two Cash rows (local order 1,0).
    resp = client.post("/s/finances/holdings/reorder/cash", data={"order": "1,0"})
    assert resp.status_code == 204
    with db_engine.connect() as conn:
        after = [a["name"] for a in get_accounts(conn, 1)]
    assert after == ["Savings", "Checking", "Visa"]


def test_holdings_add_account(client, db_engine):
    from fintrack.accounts.repository import get_accounts

    resp = client.post("/s/finances/holdings/add/cash")
    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    with db_engine.connect() as conn:
        names = [a["name"] for a in get_accounts(conn, 1)]
    assert "New account" in names


def test_holdings_add_asset(client, db_engine):
    from fintrack.networth.repository import get_asset_entries

    resp = client.post("/s/finances/holdings/add/asset")
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        names = [e["name"] for e in get_asset_entries(conn, 1)]
    assert "New asset" in names


def test_holdings_delete_asset_dispatches(client, db_engine):
    from fintrack.networth.repository import add_asset_entry, get_asset_entries

    with db_engine.connect() as conn:
        boat_id = add_asset_entry(
            conn, 1, {"kind": "asset", "name": "Boat", "value": 5000}
        )

    resp = client.post(f"/s/finances/holdings/delete/asset/{boat_id}")
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        names = [e["name"] for e in get_asset_entries(conn, 1)]
    assert "Boat" not in names


# ---------------------------------------------------------------------------
# On-demand price refresh (per-symbol + refresh-all), freshness/staleness.
# ---------------------------------------------------------------------------


def _seed_crypto(
    db_engine, *, unit="BTC", qty=2, fetched_at=None, price=Decimal("60000")
):
    """Add a non-USD asset holding, optionally seeding a cached price row so the
    Unit-Price cell has a live rate (and a known fetch age)."""
    from fintrack.networth.repository import add_asset_entry
    from fintrack.core.models import price_cache

    with db_engine.connect() as conn:
        aid = add_asset_entry(
            conn, 1, {"kind": "asset", "name": unit, "unit": unit, "quantity": qty}
        )
        if fetched_at is not None:
            conn.execute(
                price_cache.insert().values(
                    unit=unit, price_usd=price, fetched_at=fetched_at
                )
            )
            conn.commit()
    return aid


# A fresh cache means get_rates never fetches; a stale one auto-refreshes on
# load, so we patch the fetcher to a no-op (feed "down") to keep tests offline
# and to exercise the stale path — the very case the manual refresh serves.
_NO_FETCH = "fintrack.networth.prices._fetch_prices"


def test_holdings_symbol_shows_refresh_button(client, db_engine):
    _seed_crypto(db_engine, fetched_at=datetime.now(timezone.utc))
    with patch(_NO_FETCH, return_value={}):
        body = _rows_region(client.get("/s/finances/holdings").get_data(as_text=True))
    # Per-symbol refresh button posts to the single-symbol refresh route.
    assert "/holdings/refresh/BTC" in body
    # Band-level "refresh all" control is present on the Assets band.
    assert "/holdings/refresh-all" in body
    assert "Refresh prices" in body


def test_holdings_no_refresh_control_without_symbols(client):
    """A plain USD-only sheet shows neither the per-symbol nor the band control."""
    body = _rows_region(client.get("/s/finances/holdings").get_data(as_text=True))
    assert "/holdings/refresh/" not in body
    assert "/holdings/refresh-all" not in body


def test_holdings_stale_price_is_flagged(client, db_engine):
    """A price the load-time auto-refresh couldn't renew stays stale + amber."""
    old = datetime.now(timezone.utc) - timedelta(days=3)
    _seed_crypto(db_engine, fetched_at=old)
    with patch(_NO_FETCH, return_value={}):  # feed down → cache stays stale
        body = _rows_region(client.get("/s/finances/holdings").get_data(as_text=True))
    assert "is-stale" in body  # keeps the button visible + amber
    assert "updated 3d ago" in body


def test_holdings_fresh_price_not_flagged(client, db_engine):
    _seed_crypto(db_engine, fetched_at=datetime.now(timezone.utc))
    with patch(_NO_FETCH, return_value={}) as mock_fetch:
        body = _rows_region(client.get("/s/finances/holdings").get_data(as_text=True))
    mock_fetch.assert_not_called()  # a fresh cache is never refetched on load
    assert "is-stale" not in body


def test_refresh_price_force_fetches_and_updates(client, db_engine):
    from fintrack.networth.prices import _read_cache

    _seed_crypto(
        db_engine, fetched_at=datetime.now(timezone.utc), price=Decimal("60000")
    )
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal("70000")},
    ) as mock_fetch:
        resp = client.post("/s/finances/holdings/refresh/BTC", data={"edit": "0"})
    assert resp.status_code == 200
    mock_fetch.assert_called_once()  # refetched despite a fresh cache
    with db_engine.connect() as conn:
        assert _read_cache(conn, {"BTC"})["BTC"][0] == Decimal("70000")


def test_refresh_price_preserves_edit_mode(client, db_engine):
    _seed_crypto(db_engine, fetched_at=datetime.now(timezone.utc))
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal("70000")},
    ):
        resp = client.post("/s/finances/holdings/refresh/BTC", data={"edit": "1"})
    body = resp.get_data(as_text=True)
    # Edit affordances come back in the swapped tbody when edit mode is on.
    assert "/holdings/cell/" in body


def test_refresh_price_rejects_symbol_not_held(client, db_engine):
    _seed_crypto(db_engine, fetched_at=datetime.now(timezone.utc))
    with patch("fintrack.networth.prices._fetch_prices") as mock_fetch:
        resp = client.post("/s/finances/holdings/refresh/DOGE", data={"edit": "0"})
    assert resp.status_code == 404
    mock_fetch.assert_not_called()  # never reaches the external API


def test_refresh_price_rejects_bad_symbol(client):
    resp = client.post("/s/finances/holdings/refresh/not$a$symbol", data={"edit": "0"})
    assert resp.status_code == 404


def test_refresh_all_prices_refetches_every_symbol(client, db_engine):
    _seed_crypto(db_engine, unit="BTC", fetched_at=datetime.now(timezone.utc))
    _seed_crypto(db_engine, unit="ETH", fetched_at=datetime.now(timezone.utc))
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal("70000"), "ETH": Decimal("4000")},
    ) as mock_fetch:
        resp = client.post("/s/finances/holdings/refresh-all", data={"edit": "0"})
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert set(mock_fetch.call_args.args[0]) == {"BTC", "ETH"}
