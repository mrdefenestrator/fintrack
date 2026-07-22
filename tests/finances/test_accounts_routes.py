"""Tests for the accounts blueprint (web/routes/accounts.py) spreadsheet
save-error handling: unique-constraint violations must return an HTMX-friendly
422 with a visible error message, never a 500.
"""

import pytest
from sqlalchemy import create_engine

from fintrack.core.db import init_db
from fintrack.accounts.repository import add_account, get_accounts
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with schema applied."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    """Flask test client with a 'finances' snapshot and two seeded accounts."""
    from web.app import create_app

    with db_engine.connect() as conn:
        snap_id = create_snapshot(conn, "finances")
        add_account(
            conn,
            snap_id,
            {
                "name": "Wallet",
                "type": "wallet",
                "institution": "Venmo",
                "balance": 10,
            },
        )
        add_account(
            conn,
            snap_id,
            {
                "name": "Cash",
                "type": "wallet",
                "institution": "Venmo",
                "balance": 20,
            },
        )

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _account_ids(db_engine):
    with db_engine.connect() as conn:
        accs = get_accounts(conn, 1)
    return {a["name"]: a["id"] for a in accs}


# ---- update ------------------------------------------------------------------


def test_update_duplicate_name_returns_422_with_error(client, db_engine):
    ids = _account_ids(db_engine)
    resp = client.post(
        f"/s/finances/accounts/update/{ids['Cash']}",
        data={"field": "name", "value": "Wallet"},
    )
    assert resp.status_code == 422
    body = resp.get_data(as_text=True)
    assert "An account named &#34;Wallet&#34; already exists" in body
    assert "institution &#34;Venmo&#34;" in body
    # tbody partial still renders normally (page stays interactive)
    assert "accounts-add-row" in body
    # stored value unchanged
    with db_engine.connect() as conn:
        names = {a["name"] for a in get_accounts(conn, 1)}
    assert names == {"Wallet", "Cash"}


def test_update_duplicate_institution_returns_422_with_error(client, db_engine):
    """Renaming the institution (not the name) into a collision also surfaces."""
    with db_engine.connect() as conn:
        add_account(
            conn,
            1,
            {
                "name": "Wallet",
                "type": "wallet",
                "institution": "PayPal",
                "balance": 5,
            },
        )
    with db_engine.connect() as conn:
        paypal_id = next(
            a["id"] for a in get_accounts(conn, 1) if a.get("institution") == "PayPal"
        )
    resp = client.post(
        f"/s/finances/accounts/update/{paypal_id}",
        data={"field": "institution", "value": "Venmo"},
    )
    assert resp.status_code == 422
    body = resp.get_data(as_text=True)
    assert "An account named &#34;Wallet&#34; already exists" in body
    assert "institution &#34;Venmo&#34;" in body


def test_update_rename_to_unique_name_succeeds(client, db_engine):
    ids = _account_ids(db_engine)
    resp = client.post(
        f"/s/finances/accounts/update/{ids['Cash']}",
        data={"field": "name", "value": "Petty Cash"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        names = {a["name"] for a in get_accounts(conn, 1)}
    assert "Petty Cash" in names


# ---- add ---------------------------------------------------------------------


def test_add_duplicate_returns_422_with_error(client, db_engine):
    resp = client.post(
        "/s/finances/accounts/add",
        data={
            "name": "Wallet",
            "type": "wallet",
            "institution": "Venmo",
            "balance": "0",
        },
    )
    assert resp.status_code == 422
    body = resp.get_data(as_text=True)
    assert "An account named &#34;Wallet&#34; already exists" in body
    assert "institution &#34;Venmo&#34;" in body
    # response retargets onto the tbody so HTMX doesn't insert a stray row
    assert resp.headers.get("HX-Retarget") == "#accounts-tbody"
    assert resp.headers.get("HX-Reswap") == "innerHTML"
    with db_engine.connect() as conn:
        wallets = [a for a in get_accounts(conn, 1) if a["name"] == "Wallet"]
    assert len(wallets) == 1  # nothing was added


def test_add_same_name_different_institution_succeeds(client, db_engine):
    resp = client.post(
        "/s/finances/accounts/add",
        data={
            "name": "Wallet",
            "type": "wallet",
            "institution": "PayPal",
            "balance": "0",
        },
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        wallets = [a for a in get_accounts(conn, 1) if a["name"] == "Wallet"]
    assert {a["institution"] for a in wallets} == {"Venmo", "PayPal"}


# ---- Reserve (minimum_balance) editability -----------------------------------


def test_reserve_editable_only_for_cash_like_types():
    from web.routes.common import account_field_editable

    for t in ("checking", "savings", "wallet", "digital_wallet"):
        assert account_field_editable({"type": t}, "minimum_balance"), t
    for t in ("credit_card", "gift_card", "loan"):
        assert not account_field_editable({"type": t}, "minimum_balance"), t


def test_update_reserve_on_wallet_persists(client, db_engine):
    from decimal import Decimal

    ids = _account_ids(db_engine)
    resp = client.post(
        f"/s/finances/accounts/update/{ids['Cash']}",
        data={"field": "minimum_balance", "value": "250"},
    )
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        acc = next(a for a in get_accounts(conn, 1) if a["name"] == "Cash")
    assert acc["minimum_balance"] == Decimal("250")


def test_update_reserve_on_credit_card_is_rejected(client, db_engine):
    with db_engine.connect() as conn:
        cc_id = add_account(
            conn,
            1,
            {
                "name": "Visa",
                "type": "credit_card",
                "institution": "Chase",
                "limit": 5000,
                "available": 5000,
            },
        )
    resp = client.post(
        f"/s/finances/accounts/update/{cc_id}",
        data={"field": "minimum_balance", "value": "250"},
    )
    # Non-editable field: request is a no-op, value never stored.
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        acc = next(a for a in get_accounts(conn, 1) if a["id"] == cc_id)
    assert acc.get("minimum_balance") is None
