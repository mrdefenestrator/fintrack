"""Dated values for loans and assets (#107): every value change lands in
balance_history, and balances_on() reads any past date back."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from fintrack.accounts.balance_history import balances_on, get_balance_history
from fintrack.core.db import get_engine, init_db
from fintrack.core.models import balance_history, holdings
from fintrack.networth.prices import _write_cache, refresh_units
from fintrack.networth.repository import (
    add_asset_entry,
    get_asset_entries,
    record_baselines,
    record_price_points,
    update_asset_entry,
)
from fintrack.snapshots.repository import create_snapshot

D1 = date(2026, 9, 15)
D2 = date(2026, 10, 3)
D3 = date(2026, 11, 7)


@pytest.fixture()
def conn(tmp_path):
    engine = get_engine(tmp_path / "t.db")
    init_db(engine)
    with engine.connect() as connection:
        yield connection
    engine.dispose()


@pytest.fixture()
def snap(conn):
    return create_snapshot(conn, "H")


def _points(conn, holding_id):
    return [
        (p["as_of"], p["balance"], p["source"])
        for p in get_balance_history(conn, holding_id)
    ]


def _loan(conn, snap, balance=Decimal(10000), **extra):
    add_asset_entry(
        conn,
        snap,
        {"kind": "debt", "type": "loan", "name": "Car", "balance": balance, **extra},
        today=D1,
    )
    return next(e["id"] for e in get_asset_entries(conn, snap) if e["kind"] == "debt")


# --- loans ------------------------------------------------------------------


def test_loan_add_records_signed_starting_balance(conn, snap):
    loan_id = _loan(conn, snap)
    assert _points(conn, loan_id) == [(D1, Decimal(-10000), "manual")]


def test_loan_add_dates_point_by_as_of(conn, snap):
    loan_id = _loan(conn, snap, asOfDate="2026-08-01")
    assert _points(conn, loan_id)[0][0] == date(2026, 8, 1)


def test_loan_balance_edit_records_point_and_keeps_cache(conn, snap):
    loan_id = _loan(conn, snap)
    update_asset_entry(conn, snap, loan_id, {"balance": Decimal(9500)}, today=D2)
    assert _points(conn, loan_id) == [
        (D1, Decimal(-10000), "manual"),
        (D2, Decimal(-9500), "manual"),
    ]
    entry = next(e for e in get_asset_entries(conn, snap) if e["id"] == loan_id)
    assert entry["balance"] == Decimal(9500)  # dict API: positive owed
    assert entry["asOfDate"] == D2.isoformat()


def test_loan_edit_without_value_change_records_nothing(conn, snap):
    loan_id = _loan(conn, snap)
    update_asset_entry(conn, snap, loan_id, {"name": "Truck"}, today=D2)
    update_asset_entry(conn, snap, loan_id, {"balance": Decimal(10000)}, today=D2)
    assert len(_points(conn, loan_id)) == 1


# --- assets -----------------------------------------------------------------


def test_asset_add_and_value_edit_record_points(conn, snap):
    asset_id = add_asset_entry(
        conn,
        snap,
        {"kind": "asset", "name": "House", "value": Decimal(300000)},
        today=D1,
    )
    update_asset_entry(conn, snap, asset_id, {"value": Decimal(310000)}, today=D2)
    assert _points(conn, asset_id) == [
        (D1, Decimal(300000), "manual"),
        (D2, Decimal(310000), "manual"),
    ]


def test_asset_quantity_edit_uses_cached_price(conn, snap):
    _write_cache(conn, {"BTC": Decimal(50000)})
    asset_id = add_asset_entry(
        conn,
        snap,
        {"kind": "asset", "name": "Coins", "unit": "BTC", "quantity": Decimal(2)},
        today=D1,
    )
    update_asset_entry(conn, snap, asset_id, {"quantity": Decimal(3)}, today=D2)
    assert _points(conn, asset_id) == [
        (D1, Decimal(100000), "manual"),
        (D2, Decimal(150000), "manual"),
    ]


def test_asset_keeps_one_point_per_day(conn, snap):
    asset_id = add_asset_entry(
        conn, snap, {"kind": "asset", "name": "House", "value": Decimal(1)}, today=D1
    )
    update_asset_entry(conn, snap, asset_id, {"value": Decimal(2)}, today=D1)
    update_asset_entry(conn, snap, asset_id, {"value": Decimal(3)}, today=D1)
    assert _points(conn, asset_id) == [(D1, Decimal(3), "manual")]


# --- price refreshes --------------------------------------------------------


def _coins(conn, snap, quantity=Decimal(2), today=D1):
    _write_cache(conn, {"BTC": Decimal(50000)})
    return add_asset_entry(
        conn,
        snap,
        {"kind": "asset", "name": "Coins", "unit": "BTC", "quantity": quantity},
        today=today,
    )


def test_price_refresh_records_price_point_for_every_holder(conn, snap):
    other = create_snapshot(conn, "Other")
    mine = _coins(conn, snap)
    theirs = _coins(conn, other, quantity=Decimal(1))
    record_price_points(conn, {"BTC": Decimal(60000)}, today=D2)
    assert _points(conn, mine)[-1] == (D2, Decimal(120000), "price")
    assert _points(conn, theirs)[-1] == (D2, Decimal(60000), "price")


def test_price_refresh_does_not_move_as_of(conn, snap):
    asset_id = _coins(conn, snap)
    record_price_points(conn, {"BTC": Decimal(60000)}, today=D2)
    as_of = conn.execute(
        select(holdings.c.as_of_date).where(holdings.c.id == asset_id)
    ).scalar()
    assert as_of == D1


def test_price_refresh_skips_unchanged_value(conn, snap):
    asset_id = _coins(conn, snap)
    record_price_points(conn, {"BTC": Decimal(50000)}, today=D2)
    assert len(_points(conn, asset_id)) == 1


def test_price_refresh_same_day_updates_existing_point(conn, snap):
    """A refresh on a day the user already edited keeps the day 'manual' with
    the new value; a later manual edit wins over a same-day refresh."""
    asset_id = _coins(conn, snap)
    record_price_points(conn, {"BTC": Decimal(60000)}, today=D1)
    assert _points(conn, asset_id) == [(D1, Decimal(120000), "manual")]

    record_price_points(conn, {"BTC": Decimal(70000)}, today=D2)
    update_asset_entry(conn, snap, asset_id, {"quantity": Decimal(1)}, today=D2)
    # 1 coin at the cached price (the cache still says 50000)
    assert _points(conn, asset_id)[-1] == (D2, Decimal(50000), "manual")


def test_refresh_units_records_points(conn, snap):
    asset_id = _coins(conn, snap)
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal(55000)},
    ):
        refresh_units(conn, {"BTC"})
    assert _points(conn, asset_id)[-1][1:] == (Decimal(110000), "price")


# --- read back ---------------------------------------------------------------


def test_balances_on_reads_month_ends(conn, snap):
    loan_id = _loan(conn, snap)
    asset_id = _coins(conn, snap, today=D1)
    update_asset_entry(conn, snap, loan_id, {"balance": Decimal(9500)}, today=D2)
    record_price_points(conn, {"BTC": Decimal(60000)}, today=D3)
    ids = [loan_id, asset_id]

    assert balances_on(conn, ids, date(2026, 8, 31)) == {}
    assert balances_on(conn, ids, date(2026, 9, 30)) == {
        loan_id: Decimal(-10000),
        asset_id: Decimal(100000),
    }
    assert balances_on(conn, ids, date(2026, 10, 31)) == {
        loan_id: Decimal(-9500),
        asset_id: Decimal(100000),
    }
    assert balances_on(conn, ids, date(2026, 11, 30))[asset_id] == Decimal(120000)


# --- baselines --------------------------------------------------------------


def test_record_baselines_fills_holdings_without_history(conn, snap):
    loan_id = _loan(conn, snap)
    _write_cache(conn, {"ETH": Decimal(3000)})
    # Insert an asset without going through the repository (like the legacy
    # import does), so it has no history yet.
    from fintrack.core.holdings import insert_holding

    eth_id = insert_holding(
        conn,
        snap,
        "asset",
        {"name": "Eth", "as_of_date": D2},
        {"unit": "ETH", "quantity": Decimal(2)},
    )
    assert record_baselines(conn) == [eth_id]
    rows = conn.execute(
        select(balance_history).where(balance_history.c.source == "baseline")
    ).all()
    assert [(r.account_id, r.as_of, r.balance) for r in rows] == [
        (eth_id, D2, Decimal(6000))
    ]
    assert len(_points(conn, loan_id)) == 1  # untouched: it already had history


# --- CLI ---------------------------------------------------------------------


def test_cli_assets_and_debts_history(tmp_path):
    from click.testing import CliRunner

    from fintrack.cli import cli

    db_path = tmp_path / "cli.db"
    engine = get_engine(db_path)
    init_db(engine)
    with engine.connect() as c:
        create_snapshot(c, "home")
    engine.dispose()

    base = ["--db", str(db_path), "--snapshot", "home"]
    run = CliRunner().invoke
    for args in (
        ["debts", "add", "--name", "Car", "--balance", "10000"],
        ["debts", "edit", "0", "--balance", "9500", "--asOfDate", "2099-01-01"],
        ["assets", "add", "--name", "House", "--value", "300000"],
    ):
        result = run(cli, base + args)
        assert result.exit_code == 0, result.output

    result = run(cli, base + ["debts", "history", "0"])
    assert result.exit_code == 0, result.output
    assert "$10,000.00" in result.output  # amount owed, positive like `debts`
    assert "2099-01-01" in result.output
    assert "$9,500.00" in result.output

    result = run(cli, base + ["assets", "history", "0"])
    assert result.exit_code == 0, result.output
    assert "$300,000.00" in result.output
    assert "manual" in result.output


def test_yaml_import_gives_loans_and_assets_a_baseline(tmp_path):
    from pathlib import Path

    from fintrack.migrate.yaml_import import import_yaml

    engine = get_engine(tmp_path / "y.db")
    init_db(engine)
    fixture = Path(__file__).parent / "fixtures" / "test_finances.yaml"
    with engine.connect() as c:
        snap = import_yaml(c, fixture, name="y")
        entries = get_asset_entries(c, snap)
        assert entries
        found = balances_on(c, [e["id"] for e in entries], date(2999, 1, 1))
        assert set(found) == {e["id"] for e in entries}
    engine.dispose()
