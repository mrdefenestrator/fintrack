"""Tests for the categories blueprint (web/routes/categories.py): the
dedicated Categories page (shared sheet UI) — add / rename (inline,
cascading) / delete (blocked when in use)."""

import pytest
from sqlalchemy import create_engine, insert, select

from fintrack.core.db import init_db
from fintrack.core.models import budget_entries, categories, merchant_cache
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        create_snapshot(conn, "ledger")
        conn.commit()
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _add(db_engine, name):
    with db_engine.connect() as conn:
        conn.execute(insert(categories).values(name=name, sort_order=1))
        conn.commit()
    with db_engine.connect() as conn:
        return conn.execute(
            select(categories.c.id).where(categories.c.name == name)
        ).scalar()


def test_page_lists_categories(client, db_engine):
    _add(db_engine, "Groceries")
    resp = client.get("/s/ledger/categories")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No categories yet." not in body
    # The sheet lists the category inside its tbody.
    tbody_start = body.index('id="categories-table"')
    tbody_html = body[tbody_start : tbody_start + 2000]
    assert "Groceries" in tbody_html


def test_merchants_page_no_longer_embeds_categories(client, db_engine):
    _add(db_engine, "Groceries")
    body = client.get("/s/ledger/merchants").get_data(as_text=True)
    assert "Manage categories" not in body
    assert "categories-panel-body" not in body


def test_add_category_creates_stub(client, db_engine):
    resp = client.post("/s/ledger/categories/add")
    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "New category")
        ).fetchone()


def test_add_category_generates_unique_name(client, db_engine):
    _add(db_engine, "New category")
    resp = client.post("/s/ledger/categories/add")
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "New category 2")
        ).fetchone()


def test_add_category_generates_sequential_names(client, db_engine):
    _add(db_engine, "New category")
    _add(db_engine, "New category 2")
    resp = client.post("/s/ledger/categories/add")
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "New category 3")
        ).fetchone()


def test_edit_returns_inline_editor(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.get(f"/s/ledger/categories/{cat_id}/cell?field=name")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="value"' in body
    assert 'value="Groceries"' in body


def test_row_reverts_to_display(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.get(f"/s/ledger/categories/{cat_id}/cell?display=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="value"' not in body
    assert "Groceries" in body


def test_rename_persists(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.post(f"/s/ledger/categories/{cat_id}/rename", data={"value": "Food"})
    assert resp.status_code == 200
    assert "Food" in resp.get_data(as_text=True)
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "Food")
        ).fetchone()
        assert not conn.execute(
            select(categories.c.id).where(categories.c.name == "Groceries")
        ).fetchone()


def test_rename_cascades_to_merchant_cache(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    with db_engine.connect() as conn:
        conn.execute(
            insert(merchant_cache).values(
                merchant_name="WHOLE FOODS", category="Groceries", source="api"
            )
        )
        conn.commit()

    resp = client.post(f"/s/ledger/categories/{cat_id}/rename", data={"value": "Food"})
    assert resp.status_code == 200

    with db_engine.connect() as conn:
        cat = conn.execute(
            select(merchant_cache.c.category).where(
                merchant_cache.c.merchant_name == "WHOLE FOODS"
            )
        ).scalar()
    assert cat == "Food"


def test_rename_to_duplicate_returns_422_with_message(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    _add(db_engine, "Dining")
    resp = client.post(
        f"/s/ledger/categories/{cat_id}/rename", data={"value": "Dining"}
    )
    assert resp.status_code == 422
    assert "HX-Refresh" not in resp.headers
    assert "already exists" in resp.get_data(as_text=True)
    # Original name preserved.
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "Groceries")
        ).fetchone()


def test_rename_unknown_category_404(client):
    resp = client.post("/s/ledger/categories/999999/rename", data={"value": "Food"})
    assert resp.status_code == 404


def test_delete_confirm_shows_yes_no(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.get(f"/s/ledger/categories/{cat_id}/delete-confirm")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Confirm delete" in body
    assert "Cancel delete" in body


def test_delete_btn_cancels_confirm(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.get(f"/s/ledger/categories/{cat_id}/delete-btn")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Confirm delete" not in body
    assert "Delete" in body


def test_delete_unused_category_succeeds(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    resp = client.post(f"/s/ledger/categories/{cat_id}/delete")
    assert resp.status_code == 200
    assert resp.headers.get("HX-Refresh") == "true"
    with db_engine.connect() as conn:
        assert not conn.execute(
            select(categories.c.id).where(categories.c.name == "Groceries")
        ).fetchone()


def test_delete_blocked_shows_breakdown_message(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    with db_engine.connect() as conn:
        conn.execute(
            insert(merchant_cache).values(
                merchant_name="WHOLE FOODS", category="Groceries", source="api"
            )
        )
        conn.commit()

    resp = client.post(f"/s/ledger/categories/{cat_id}/delete")
    assert resp.status_code == 422
    assert "HX-Refresh" not in resp.headers
    body = resp.get_data(as_text=True)
    assert "In use by" in body
    assert "1 merchant" in body
    # Category must still exist.
    with db_engine.connect() as conn:
        assert conn.execute(
            select(categories.c.id).where(categories.c.name == "Groceries")
        ).fetchone()


def test_delete_blocked_by_budget_entry(client, db_engine):
    cat_id = _add(db_engine, "Groceries")
    with db_engine.connect() as conn:
        snapshot_id = create_snapshot(conn, "budget-snap")
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

    resp = client.post(f"/s/ledger/categories/{cat_id}/delete")
    assert resp.status_code == 422
    assert "1 budget entry" in resp.get_data(as_text=True)


def test_delete_unknown_category_404(client):
    resp = client.post("/s/ledger/categories/999999/delete")
    assert resp.status_code == 404
