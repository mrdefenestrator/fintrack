from spending.repository.categories import (
    seed_categories,
    list_categories,
    add_category,
    get_category_names,
    delete_category,
)


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


def test_add_category(conn):
    add_category(conn, name="Pets", sort_order=15)
    cats = list_categories(conn)
    names = [c["name"] for c in cats]
    assert "Pets" in names


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
