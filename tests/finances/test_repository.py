"""Tests for the SQLite repository layer (accounts, budget, assets, snapshots)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from fintrack.core.db import init_db
from fintrack.accounts.repository import (
    add_account,
    delete_account,
    get_accounts,
    move_account,
    reorder_accounts,
    update_account,
)
from fintrack.networth.repository import (
    add_asset_entry,
    delete_asset_entry,
    get_asset_entries,
    move_asset_entry,
    update_asset_entry,
)
from fintrack.budget.repository import (
    add_budget_entry,
    delete_budget_entry,
    get_budget_entries,
    move_budget_entry,
    reorder_budget_entries,
    update_budget_entry,
)
from fintrack.snapshots.repository import (
    copy_snapshot,
    create_snapshot,
    delete_snapshot,
    get_snapshot_id,
    list_snapshots,
    rename_snapshot,
)
from fintrack.core.loader import load_finances_from_db


@pytest.fixture()
def conn():
    """In-memory SQLite connection with schema and a single test snapshot."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as c:
        snap_id = create_snapshot(c, "test")
        # Seed accounts
        add_account(
            c, snap_id, {"name": "Checking", "type": "checking", "balance": 1000}
        )
        add_account(c, snap_id, {"name": "Savings", "type": "savings", "balance": 5000})
        # Seed budget
        add_budget_entry(
            c,
            snap_id,
            {
                "kind": "income",
                "description": "Salary",
                "amount": 5000,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            },
        )
        add_budget_entry(
            c,
            snap_id,
            {
                "kind": "income",
                "description": "Bonus",
                "amount": 1000,
                "recurrence": "one_time",
                "date": "2025-12-25",
            },
        )
        add_budget_entry(
            c,
            snap_id,
            {
                "kind": "expense",
                "description": "Rent",
                "amount": 1500,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            },
        )
        add_budget_entry(
            c,
            snap_id,
            {
                "kind": "expense",
                "description": "Food",
                "amount": 500,
                "recurrence": "monthly",
                "continuous": True,
            },
        )
        # Seed assets: Home(asset), Car(asset), Mortgage(debt), Car Loan(debt)
        home_id = add_asset_entry(
            c, snap_id, {"kind": "asset", "name": "Home", "value": 500000}
        )
        car_id = add_asset_entry(
            c, snap_id, {"kind": "asset", "name": "Car", "value": 25000}
        )
        add_asset_entry(
            c,
            snap_id,
            {
                "kind": "debt",
                "name": "Mortgage",
                "balance": 400000,
                "assetRef": home_id,
            },
        )
        add_asset_entry(
            c,
            snap_id,
            {"kind": "debt", "name": "Car Loan", "balance": 15000, "assetRef": car_id},
        )
        yield c, snap_id


# =============================================================================
# Snapshot tests
# =============================================================================


def test_list_snapshots(conn):
    c, _ = conn
    names = list_snapshots(c)
    assert names == ["test"]


def test_get_snapshot_id(conn):
    c, snap_id = conn
    assert get_snapshot_id(c, "test") == snap_id
    assert get_snapshot_id(c, "nonexistent") is None


def test_rename_snapshot(conn):
    c, snap_id = conn
    rename_snapshot(c, snap_id, "renamed")
    assert get_snapshot_id(c, "renamed") == snap_id
    assert get_snapshot_id(c, "test") is None


def test_copy_snapshot(conn):
    c, snap_id = conn
    new_id = copy_snapshot(c, snap_id, "copy")
    assert new_id != snap_id
    accs = get_accounts(c, new_id)
    assert len(accs) == 2
    assert accs[0]["name"] == "Checking"
    budget = get_budget_entries(c, new_id)
    assert len(budget) == 4
    assets = get_asset_entries(c, new_id)
    assert len(assets) == 4
    # assetRef should be rewritten to new asset IDs
    debts = [a for a in assets if a["kind"] == "debt"]
    asset_ids = {a["id"] for a in assets if a["kind"] == "asset"}
    for debt in debts:
        assert debt.get("assetRef") in asset_ids


def test_delete_snapshot(conn):
    c, snap_id = conn
    delete_snapshot(c, snap_id)
    assert get_snapshot_id(c, "test") is None
    # Child rows are removed by ON DELETE CASCADE, even though budget/asset
    # entries carry NO ACTION cross-references to accounts/assets.
    assert get_accounts(c, snap_id) == []
    assert get_budget_entries(c, snap_id) == []
    assert get_asset_entries(c, snap_id) == []


def test_foreign_keys_enforced(conn):
    """PRAGMA foreign_keys is ON: a budget entry cannot reference a
    nonexistent account."""
    c, snap_id = conn
    with pytest.raises(IntegrityError):
        add_budget_entry(
            c,
            snap_id,
            {
                "kind": "income",
                "description": "Bad ref",
                "amount": 100,
                "recurrence": "monthly",
                "autoAccountRef": 999999,
            },
        )
    c.rollback()


# =============================================================================
# Account tests
# =============================================================================


def test_add_account(conn):
    c, snap_id = conn
    new_id = add_account(
        c, snap_id, {"name": "Gift Card", "type": "gift_card", "balance": 50}
    )
    accs = get_accounts(c, snap_id)
    assert len(accs) == 3
    new_acc = next(a for a in accs if a["id"] == new_id)
    assert new_acc["name"] == "Gift Card"


def test_update_account(conn):
    c, snap_id = conn
    accs = get_accounts(c, snap_id)
    acc_id = accs[0]["id"]
    update_account(c, snap_id, acc_id, {"name": "Main Checking", "balance": 2000})
    updated = next(a for a in get_accounts(c, snap_id) if a["id"] == acc_id)
    assert updated["name"] == "Main Checking"
    assert updated["balance"] == 2000


def test_update_account_not_found(conn):
    c, snap_id = conn
    with pytest.raises(ValueError, match="not found"):
        update_account(c, snap_id, 9999, {"name": "X"})


def test_same_name_different_institution_allowed(conn):
    """UNIQUE is (snapshot_id, institution, name): two 'Wallet' accounts under
    different institutions can coexist."""
    c, snap_id = conn
    add_account(
        c,
        snap_id,
        {"name": "Wallet", "type": "wallet", "institution": "Venmo", "balance": 10},
    )
    add_account(
        c,
        snap_id,
        {"name": "Wallet", "type": "wallet", "institution": "PayPal", "balance": 20},
    )
    wallets = [a for a in get_accounts(c, snap_id) if a["name"] == "Wallet"]
    assert {a["institution"] for a in wallets} == {"Venmo", "PayPal"}


def test_same_name_same_institution_rejected(conn):
    c, snap_id = conn
    add_account(
        c,
        snap_id,
        {"name": "Wallet", "type": "wallet", "institution": "Venmo", "balance": 10},
    )
    with pytest.raises(IntegrityError):
        add_account(
            c,
            snap_id,
            {"name": "Wallet", "type": "wallet", "institution": "Venmo", "balance": 20},
        )
    c.rollback()


def test_update_to_duplicate_name_same_institution_rejected(conn):
    c, snap_id = conn
    add_account(
        c,
        snap_id,
        {"name": "Wallet", "type": "wallet", "institution": "Venmo", "balance": 10},
    )
    other_id = add_account(
        c,
        snap_id,
        {"name": "Cash", "type": "wallet", "institution": "Venmo", "balance": 20},
    )
    with pytest.raises(IntegrityError):
        update_account(c, snap_id, other_id, {"name": "Wallet"})
    c.rollback()


def test_delete_account(conn):
    c, snap_id = conn
    accs = get_accounts(c, snap_id)
    acc_id = accs[1]["id"]
    delete_account(c, snap_id, acc_id)
    remaining = get_accounts(c, snap_id)
    assert len(remaining) == 1
    assert all(a["id"] != acc_id for a in remaining)


def test_delete_account_not_found(conn):
    c, snap_id = conn
    with pytest.raises(ValueError, match="not found"):
        delete_account(c, snap_id, 9999)


def test_delete_account_referenced_by_budget(conn):
    c, snap_id = conn
    accs = get_accounts(c, snap_id)
    acc_id = accs[0]["id"]
    add_budget_entry(
        c,
        snap_id,
        {
            "kind": "income",
            "description": "Direct Deposit",
            "amount": 100,
            "recurrence": "monthly",
            "autoAccountRef": acc_id,
        },
    )
    with pytest.raises(ValueError, match="referenced by a budget entry"):
        delete_account(c, snap_id, acc_id)


def test_move_account(conn):
    c, snap_id = conn
    accs = get_accounts(c, snap_id)
    first_id, second_id = accs[0]["id"], accs[1]["id"]
    move_account(c, snap_id, second_id, "up")
    accs = get_accounts(c, snap_id)
    assert accs[0]["id"] == second_id
    assert accs[1]["id"] == first_id


def test_move_account_boundary(conn):
    c, snap_id = conn
    accs = get_accounts(c, snap_id)
    first_id = accs[0]["id"]
    move_account(c, snap_id, first_id, "up")  # no-op at top
    assert get_accounts(c, snap_id)[0]["id"] == first_id


def test_reorder_accounts(conn):
    c, snap_id = conn
    # Third account so the permutation is non-trivial.
    add_account(c, snap_id, {"name": "Third", "type": "savings", "balance": 0})
    ids = [a["id"] for a in get_accounts(c, snap_id)]  # positions 0,1,2
    # Move the last row to the front: new_order[k] = old position now at k.
    reorder_accounts(c, snap_id, [2, 0, 1])
    assert [a["id"] for a in get_accounts(c, snap_id)] == [ids[2], ids[0], ids[1]]


def test_reorder_accounts_rejects_non_permutation(conn):
    c, snap_id = conn  # fixture seeds 2 accounts
    with pytest.raises(ValueError):
        reorder_accounts(c, snap_id, [0, 0])  # not a permutation of range(2)
    with pytest.raises(ValueError):
        reorder_accounts(c, snap_id, [0])  # wrong length


def test_reorder_budget_entries(conn):
    c, snap_id = conn  # fixture seeds 4 budget entries (Salary, Bonus, Rent, Food)
    before = [e["description"] for e in get_budget_entries(c, snap_id)]
    reorder_budget_entries(c, snap_id, [3, 2, 1, 0])  # reverse
    after = [e["description"] for e in get_budget_entries(c, snap_id)]
    assert after == list(reversed(before))


# =============================================================================
# Budget tests
# =============================================================================


def test_add_budget_entry(conn):
    c, snap_id = conn
    add_budget_entry(
        c,
        snap_id,
        {
            "kind": "income",
            "description": "Refund",
            "amount": 100,
            "recurrence": "one_time",
            "date": "2025-03-01",
        },
    )
    entries = get_budget_entries(c, snap_id)
    assert len(entries) == 5
    assert entries[4]["description"] == "Refund"


def test_update_budget_entry(conn):
    c, snap_id = conn
    update_budget_entry(c, snap_id, 0, {"amount": 6000})
    entries = get_budget_entries(c, snap_id)
    assert entries[0]["amount"] == 6000


def test_update_budget_entry_out_of_range(conn):
    c, snap_id = conn
    with pytest.raises(ValueError, match="out of range"):
        update_budget_entry(c, snap_id, 99, {"amount": 1})


def test_delete_budget_entry(conn):
    c, snap_id = conn
    delete_budget_entry(c, snap_id, 1)  # Remove "Bonus" (index 1)
    entries = get_budget_entries(c, snap_id)
    assert len(entries) == 3
    assert entries[0]["description"] == "Salary"
    assert entries[1]["description"] == "Rent"


def test_move_budget_entry(conn):
    c, snap_id = conn
    # Initial: [Salary(0), Bonus(1), Rent(2), Food(3)]
    move_budget_entry(c, snap_id, 2, "up")
    entries = get_budget_entries(c, snap_id)
    assert entries[1]["description"] == "Rent"
    assert entries[2]["description"] == "Bonus"


def test_move_budget_entry_crosses_kinds(conn):
    c, snap_id = conn
    move_budget_entry(c, snap_id, 1, "down")
    entries = get_budget_entries(c, snap_id)
    assert entries[1]["description"] == "Rent"
    assert entries[2]["description"] == "Bonus"


def test_update_budget_entry_delete_keys(conn):
    """delete_keys removes a field from the entry."""
    c, snap_id = conn
    # Food (index 3) has continuous=True
    update_budget_entry(c, snap_id, 3, {}, delete_keys=["continuous"])
    entries = get_budget_entries(c, snap_id)
    assert "continuous" not in entries[3] or entries[3].get("continuous") is None


# =============================================================================
# Asset tests
# =============================================================================
# Fixture layout: assets[0]=Home(asset), assets[1]=Car(asset),
#                 assets[2]=Mortgage(debt), assets[3]=CarLoan(debt)


def test_add_asset_entry_asset(conn):
    c, snap_id = conn
    new_id = add_asset_entry(
        c, snap_id, {"kind": "asset", "name": "Stocks", "value": 10000}
    )
    assert new_id is not None
    assets = get_asset_entries(c, snap_id)
    assert len(assets) == 5
    new_entry = next(a for a in assets if a.get("id") == new_id)
    assert new_entry["kind"] == "asset"


def test_add_asset_entry_debt(conn):
    c, snap_id = conn
    result = add_asset_entry(
        c, snap_id, {"kind": "debt", "name": "Student Loan", "balance": 30000}
    )
    assert result is None
    assets = get_asset_entries(c, snap_id)
    assert len(assets) == 5
    assert assets[4]["name"] == "Student Loan"
    assert assets[4]["kind"] == "debt"
    assert "id" not in assets[4]


def test_update_asset_entry(conn):
    c, snap_id = conn
    update_asset_entry(c, snap_id, 0, {"value": 550000})
    assets = get_asset_entries(c, snap_id)
    assert assets[0]["value"] == 550000

    update_asset_entry(c, snap_id, 2, {"balance": 395000})
    assets = get_asset_entries(c, snap_id)
    assert assets[2]["balance"] == 395000


def test_delete_asset_entry_debt(conn):
    c, snap_id = conn
    delete_asset_entry(c, snap_id, 3)  # Car Loan (index 3)
    assets = get_asset_entries(c, snap_id)
    assert len(assets) == 3
    assert assets[2]["name"] == "Mortgage"


def test_delete_asset_entry_asset(conn):
    c, snap_id = conn
    delete_asset_entry(c, snap_id, 3)  # Remove Car Loan first
    delete_asset_entry(c, snap_id, 1)  # Then Car
    assets = get_asset_entries(c, snap_id)
    assert len(assets) == 2
    assert assets[0]["name"] == "Home"


def test_delete_asset_entry_referenced_by_debt(conn):
    c, snap_id = conn
    with pytest.raises(ValueError, match="referenced by a debt"):
        delete_asset_entry(c, snap_id, 0)  # Home is referenced by Mortgage


def test_move_asset_entry(conn):
    c, snap_id = conn
    move_asset_entry(c, snap_id, 1, "up")
    assets = get_asset_entries(c, snap_id)
    assert assets[0]["name"] == "Car"
    assert assets[1]["name"] == "Home"


def test_move_asset_crosses_kinds(conn):
    c, snap_id = conn
    move_asset_entry(c, snap_id, 2, "up")  # Mortgage up past Car
    assets = get_asset_entries(c, snap_id)
    assert assets[1]["name"] == "Mortgage"
    assert assets[2]["name"] == "Car"


def test_move_invalid_direction(conn):
    c, snap_id = conn
    with pytest.raises(ValueError, match="direction must be"):
        move_account(c, snap_id, 1, "left")


# =============================================================================
# load_finances_from_db integration
# =============================================================================


def test_load_finances_from_db(conn):
    c, snap_id = conn
    data = load_finances_from_db(c, snap_id)
    assert len(data["accounts"]) == 2
    assert len(data["budget"]) == 4
    assert len(data["assets"]) == 4
    # No internal _db_id keys exposed
    for entry in data["budget"] + data["assets"]:
        assert "_db_id" not in entry


def test_asset_entry_type_round_trip():
    """The liquidity-tier `type` persists and can be updated."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as c:
        snap_id = create_snapshot(c, "s")
        add_asset_entry(
            c,
            snap_id,
            {"kind": "asset", "type": "brokerage", "name": "BTC", "value": 100},
        )
        add_asset_entry(
            c,
            snap_id,
            {"kind": "debt", "type": "loan", "name": "Mortgage", "balance": 50},
        )
        by_name = {e["name"]: e for e in get_asset_entries(c, snap_id)}
        assert by_name["BTC"]["type"] == "brokerage"
        assert by_name["Mortgage"]["type"] == "loan"
        # BTC is the first entry (sort_order); reclassify it.
        update_asset_entry(c, snap_id, 0, {"type": "retirement"})
        assert get_asset_entries(c, snap_id)[0]["type"] == "retirement"


def test_asset_entry_unit_defaults_usd_and_round_trips():
    """`unit` defaults to USD and can be overridden with a symbol + updated."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as c:
        snap_id = create_snapshot(c, "s")
        add_asset_entry(c, snap_id, {"kind": "asset", "name": "Home", "value": 400000})
        add_asset_entry(
            c,
            snap_id,
            {"kind": "asset", "name": "BTC", "unit": "BTC", "quantity": 0.4},
        )
        by_name = {e["name"]: e for e in get_asset_entries(c, snap_id)}
        assert by_name["Home"]["unit"] == "USD"  # default
        assert by_name["BTC"]["unit"] == "BTC"  # override persists
        # Home is the first entry (sort_order); change its unit.
        update_asset_entry(c, snap_id, 0, {"unit": "ETH"})
        assert get_asset_entries(c, snap_id)[0]["unit"] == "ETH"
