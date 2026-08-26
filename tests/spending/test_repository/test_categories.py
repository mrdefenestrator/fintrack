import pytest
from sqlalchemy import insert, select

from fintrack.core.models import budget_entries, merchant_cache, transaction_corrections
from fintrack.ledger.repository.categories import (
    add_category,
    delete_category,
    edit_category,
    get_category_names,
    list_categories,
    move_category,
    rename_category,
    seed_categories,
)
from fintrack.snapshots.repository import create_snapshot


def test_seed_categories(conn):
    seed_categories(conn, "configs/categories.yaml")
    cats = list_categories(conn)
    assert len(cats) == 16
    assert cats[0]["name"] == "Groceries"
    assert cats[-1]["name"] == "Other"


def test_seed_categories_is_idempotent(conn):
    seed_categories(conn, "configs/categories.yaml")
    seed_categories(conn, "configs/categories.yaml")
    cats = list_categories(conn)
    assert len(cats) == 16


def test_seed_categories_does_not_resurrect_a_deleted_default(conn):
    """A user-deleted default category must stay deleted across app restarts
    (i.e. across repeated seed_categories calls, which is what every web/CLI
    startup does)."""
    seed_categories(conn, "configs/categories.yaml")
    delete_category(conn, name="Other")
    assert "Other" not in get_category_names(conn)

    # Simulate a restart: seed_categories runs again against the now-15-row
    # table. It must NOT top up the missing "Other" default.
    seed_categories(conn, "configs/categories.yaml")
    names = get_category_names(conn)
    assert "Other" not in names
    assert len(names) == 15


def test_add_category(conn):
    add_category(conn, name="Pets")
    cats = list_categories(conn)
    names = [c["name"] for c in cats]
    assert "Pets" in names


def test_add_category_appends_after_max_sort_order(conn):
    seed_categories(conn, "configs/categories.yaml")
    cats_before = list_categories(conn)
    max_order = max(c["sort_order"] for c in cats_before)

    add_category(conn, name="Pets")

    cats = list_categories(conn)
    pets = next(c for c in cats if c["name"] == "Pets")
    assert pets["sort_order"] == max_order + 1
    # Also the last in sort order.
    assert cats[-1]["name"] == "Pets"


def test_add_category_explicit_sort_order(conn):
    add_category(conn, name="Groceries", sort_order=5)
    cats = list_categories(conn)
    assert cats[0]["sort_order"] == 5


def test_add_category_rejects_exact_duplicate(conn):
    add_category(conn, name="Pets")
    with pytest.raises(ValueError, match="already exists"):
        add_category(conn, name="Pets")


def test_add_category_rejects_case_insensitive_duplicate(conn):
    add_category(conn, name="Pets")
    with pytest.raises(ValueError, match="already exists"):
        add_category(conn, name="PETS")
    with pytest.raises(ValueError, match="already exists"):
        add_category(conn, name="pets")


def test_add_category_rejects_empty_name(conn):
    with pytest.raises(ValueError, match="empty"):
        add_category(conn, name="   ")


def test_get_category_names(conn):
    seed_categories(conn, "configs/categories.yaml")
    names = get_category_names(conn)
    assert "Groceries" in names
    assert "Dining" in names
    assert len(names) == 16


def test_delete_category(conn):
    seed_categories(conn, "configs/categories.yaml")
    delete_category(conn, name="Other")
    names = get_category_names(conn)
    assert "Other" not in names


def test_delete_category_not_found(conn):
    with pytest.raises(ValueError, match="not found"):
        delete_category(conn, name="Nonexistent")


def test_delete_category_blocked_by_merchant_cache(conn):
    add_category(conn, name="Groceries")
    conn.execute(
        insert(merchant_cache).values(
            merchant_name="WHOLE FOODS", category="Groceries", source="api"
        )
    )
    conn.commit()
    with pytest.raises(ValueError) as exc_info:
        delete_category(conn, name="Groceries")
    msg = str(exc_info.value)
    assert "In use by" in msg
    assert "1 merchant" in msg
    # Category must still exist.
    assert "Groceries" in get_category_names(conn)


def test_delete_category_breakdown_message_all_three_tables(conn):
    add_category(conn, name="Groceries")
    snapshot_id = create_snapshot(conn, "s1")

    conn.execute(
        insert(merchant_cache).values(
            merchant_name="M1", category="Groceries", source="api"
        )
    )
    conn.execute(
        insert(merchant_cache).values(
            merchant_name="M2", category="Groceries", source="api"
        )
    )
    txn_id_1 = _insert_dummy_transaction(conn, snapshot_id, "T1")
    txn_id_2 = _insert_dummy_transaction(conn, snapshot_id, "T2")
    txn_id_3 = _insert_dummy_transaction(conn, snapshot_id, "T3")
    for tid in (txn_id_1, txn_id_2, txn_id_3):
        conn.execute(
            insert(transaction_corrections).values(
                transaction_id=tid, category="Groceries"
            )
        )
    conn.execute(
        insert(budget_entries).values(
            snapshot_id=snapshot_id,
            kind="expense",
            description="Weekly shop",
            amount=100,
            recurrence="monthly",
            category="Groceries",
        )
    )
    conn.commit()

    with pytest.raises(ValueError) as exc_info:
        delete_category(conn, name="Groceries")
    msg = str(exc_info.value)
    assert "2 merchants" in msg
    assert "3 corrections" in msg
    assert "1 budget entry" in msg


def _insert_dummy_transaction(conn, snapshot_id, fitid):
    from datetime import date
    from decimal import Decimal

    from fintrack.core.models import holdings, imports, transactions
    from fintrack.ledger.repository.accounts import add_account

    acc_id = conn.execute(
        select(holdings.c.id).where(holdings.c.snapshot_id == snapshot_id)
    ).scalar()
    if acc_id is None:
        acc_id = add_account(
            conn,
            name="Test Checking",
            institution="",
            account_type="checking",
            snapshot_id=snapshot_id,
        )
    import_id = conn.execute(
        insert(imports).values(
            account_id=acc_id,
            holding_group="cash",
            filename=f"{fitid}.ofx",
            file_hash=fitid,
            status="confirmed",
        )
    ).inserted_primary_key[0]
    result = conn.execute(
        insert(transactions).values(
            account_id=acc_id,
            import_id=import_id,
            date=date(2026, 1, 1),
            amount=Decimal("-10.00"),
            raw_description=fitid,
            normalized_merchant=fitid,
            fingerprint=fitid,
        )
    )
    return result.inserted_primary_key[0]


def test_rename_category(conn):
    add_category(conn, name="Groceries")
    rename_category(conn, "Groceries", "Food")
    names = get_category_names(conn)
    assert "Food" in names
    assert "Groceries" not in names


def test_rename_category_not_found(conn):
    with pytest.raises(ValueError, match="not found"):
        rename_category(conn, "Nonexistent", "New Name")


def test_rename_category_rejects_duplicate_target(conn):
    add_category(conn, name="Groceries")
    add_category(conn, name="Dining")
    with pytest.raises(ValueError, match="already exists"):
        rename_category(conn, "Groceries", "Dining")


def test_rename_category_rejects_case_insensitive_duplicate_target(conn):
    add_category(conn, name="Groceries")
    add_category(conn, name="Dining")
    with pytest.raises(ValueError, match="already exists"):
        rename_category(conn, "Groceries", "DINING")


def test_rename_category_allows_case_only_change(conn):
    add_category(conn, name="Groceries")
    rename_category(conn, "Groceries", "groceries")
    names = get_category_names(conn)
    assert "groceries" in names


def test_rename_category_cascades_to_merchant_cache(conn):
    add_category(conn, name="Groceries")
    conn.execute(
        insert(merchant_cache).values(
            merchant_name="WHOLE FOODS", category="Groceries", source="api"
        )
    )
    conn.commit()

    rename_category(conn, "Groceries", "Food")

    cat = conn.execute(
        select(merchant_cache.c.category).where(
            merchant_cache.c.merchant_name == "WHOLE FOODS"
        )
    ).scalar()
    assert cat == "Food"


def test_rename_category_cascades_to_transaction_corrections(conn):
    add_category(conn, name="Groceries")
    snapshot_id = create_snapshot(conn, "s1")
    txn_id = _insert_dummy_transaction(conn, snapshot_id, "T1")
    conn.execute(
        insert(transaction_corrections).values(
            transaction_id=txn_id, category="Groceries"
        )
    )
    conn.commit()

    rename_category(conn, "Groceries", "Food")

    cat = conn.execute(
        select(transaction_corrections.c.category).where(
            transaction_corrections.c.transaction_id == txn_id
        )
    ).scalar()
    assert cat == "Food"


def test_rename_category_cascades_to_budget_entries(conn):
    add_category(conn, name="Groceries")
    snapshot_id = create_snapshot(conn, "s1")
    conn.execute(
        insert(budget_entries).values(
            snapshot_id=snapshot_id,
            kind="expense",
            description="Weekly shop",
            amount=100,
            recurrence="monthly",
            category="Groceries",
        )
    )
    conn.commit()

    rename_category(conn, "Groceries", "Food")

    cat = conn.execute(
        select(budget_entries.c.category).where(
            budget_entries.c.snapshot_id == snapshot_id
        )
    ).scalar()
    assert cat == "Food"


def test_rename_category_cascades_across_all_three_tables_at_once(conn):
    """A single rename must update merchant_cache, transaction_corrections,
    and budget_entries together, leaving nothing pointing at the old name."""
    add_category(conn, name="Groceries")
    snapshot_id = create_snapshot(conn, "s1")
    txn_id = _insert_dummy_transaction(conn, snapshot_id, "T1")
    conn.execute(
        insert(merchant_cache).values(
            merchant_name="WHOLE FOODS", category="Groceries", source="api"
        )
    )
    conn.execute(
        insert(transaction_corrections).values(
            transaction_id=txn_id, category="Groceries"
        )
    )
    conn.execute(
        insert(budget_entries).values(
            snapshot_id=snapshot_id,
            kind="expense",
            description="Weekly shop",
            amount=100,
            recurrence="monthly",
            category="Groceries",
        )
    )
    conn.commit()

    rename_category(conn, "Groceries", "Food")

    assert (
        conn.execute(
            select(merchant_cache.c.category).where(
                merchant_cache.c.merchant_name == "WHOLE FOODS"
            )
        ).scalar()
        == "Food"
    )
    assert (
        conn.execute(
            select(transaction_corrections.c.category).where(
                transaction_corrections.c.transaction_id == txn_id
            )
        ).scalar()
        == "Food"
    )
    assert (
        conn.execute(
            select(budget_entries.c.category).where(
                budget_entries.c.snapshot_id == snapshot_id
            )
        ).scalar()
        == "Food"
    )
    # Nothing should still reference "Groceries".
    assert (
        conn.execute(
            select(merchant_cache.c.id).where(merchant_cache.c.category == "Groceries")
        ).fetchone()
        is None
    )


def test_edit_category_by_id_cascades_rename(conn):
    add_category(conn, name="Groceries")
    conn.execute(
        insert(merchant_cache).values(
            merchant_name="WHOLE FOODS", category="Groceries", source="api"
        )
    )
    conn.commit()
    cat_id = conn.execute(
        select(_categories_id()).where(_categories_name() == "Groceries")
    ).scalar()

    edit_category(conn, cat_id, name="Food")

    cat = conn.execute(
        select(merchant_cache.c.category).where(
            merchant_cache.c.merchant_name == "WHOLE FOODS"
        )
    ).scalar()
    assert cat == "Food"


def test_edit_category_rejects_duplicate_target(conn):
    add_category(conn, name="Groceries")
    add_category(conn, name="Dining")
    cat_id = conn.execute(
        select(_categories_id()).where(_categories_name() == "Groceries")
    ).scalar()
    with pytest.raises(ValueError, match="already exists"):
        edit_category(conn, cat_id, name="Dining")


def _categories_id():
    from fintrack.core.models import categories

    return categories.c.id


def _categories_name():
    from fintrack.core.models import categories

    return categories.c.name


def test_move_category_up_and_down(conn):
    add_category(conn, name="A", sort_order=1)
    add_category(conn, name="B", sort_order=2)
    add_category(conn, name="C", sort_order=3)

    move_category(conn, "B", "up")
    names = get_category_names(conn)
    assert names == ["B", "A", "C"]

    move_category(conn, "B", "down")
    names = get_category_names(conn)
    assert names == ["A", "B", "C"]


def test_move_category_at_top_is_noop(conn):
    add_category(conn, name="A", sort_order=1)
    add_category(conn, name="B", sort_order=2)
    move_category(conn, "A", "up")
    assert get_category_names(conn) == ["A", "B"]


def test_move_category_at_bottom_is_noop(conn):
    add_category(conn, name="A", sort_order=1)
    add_category(conn, name="B", sort_order=2)
    move_category(conn, "B", "down")
    assert get_category_names(conn) == ["A", "B"]


def test_move_category_not_found(conn):
    with pytest.raises(ValueError, match="not found"):
        move_category(conn, "Nonexistent", "up")


def test_move_category_invalid_direction(conn):
    add_category(conn, name="A")
    with pytest.raises(ValueError, match="direction"):
        move_category(conn, "A", "sideways")
