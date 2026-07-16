"""Tests for the files blueprint (web/routes/files.py) using the DB backend."""

import pytest
from sqlalchemy import create_engine

from finances.db import init_db
from finances.repository.snapshots import create_snapshot, get_snapshot_id


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine with schema applied."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    return engine


@pytest.fixture()
def client(db_engine):
    """Flask test client with an in-memory DB that has one 'finances' snapshot."""
    from web.app import create_app

    with db_engine.connect() as conn:
        create_snapshot(conn, "finances")

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


HX_HEADERS = {"HX-Request": "true"}


# ---- New --------------------------------------------------------------------


def test_new_snapshot(client, db_engine):
    resp = client.post("/files/new", data={"name": "budget-2026"}, headers=HX_HEADERS)
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/f/budget-2026/accounts"
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "budget-2026") is not None


def test_new_snapshot_already_exists(client, db_engine):
    resp = client.post("/files/new", data={"name": "finances"})
    assert resp.status_code == 409


def test_new_snapshot_invalid_name(client, db_engine):
    resp = client.post("/files/new", data={"name": "   "})
    assert resp.status_code == 400


def test_new_snapshot_path_traversal(client, db_engine):
    # rsplit("/") keeps only the last component, so "../etc/passwd" → "passwd"
    resp = client.post("/files/new", data={"name": "../etc/passwd"}, headers=HX_HEADERS)
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "passwd") is not None


# ---- Copy -------------------------------------------------------------------


def test_copy_snapshot(client, db_engine):
    resp = client.post(
        "/files/copy",
        data={"source": "finances", "name": "finances-copy"},
        headers=HX_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/f/finances-copy/accounts"
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "finances-copy") is not None


def test_copy_nonexistent_source(client, db_engine):
    resp = client.post("/files/copy", data={"source": "nope", "name": "copy"})
    assert resp.status_code == 404


def test_copy_dest_already_exists(client, db_engine):
    resp = client.post("/files/copy", data={"source": "finances", "name": "finances"})
    assert resp.status_code == 409


# ---- Rename -----------------------------------------------------------------


def test_rename_snapshot(client, db_engine):
    resp = client.post(
        "/files/rename",
        data={"old_name": "finances", "new_name": "renamed"},
        headers=HX_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/f/renamed/accounts"
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "finances") is None
        assert get_snapshot_id(conn, "renamed") is not None


def test_rename_nonexistent(client, db_engine):
    resp = client.post(
        "/files/rename",
        data={"old_name": "nope", "new_name": "renamed"},
    )
    assert resp.status_code == 404


def test_rename_dest_exists(client, db_engine):
    with db_engine.connect() as conn:
        create_snapshot(conn, "other")
    resp = client.post(
        "/files/rename",
        data={"old_name": "finances", "new_name": "other"},
    )
    assert resp.status_code == 409


# ---- Delete -----------------------------------------------------------------


def test_delete_snapshot(client, db_engine):
    with db_engine.connect() as conn:
        create_snapshot(conn, "deleteme")
    resp = client.post("/files/delete", data={"name": "deleteme"}, headers=HX_HEADERS)
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/f/finances/accounts"
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "deleteme") is None


def test_delete_only_snapshot(client, db_engine):
    resp = client.post("/files/delete", data={"name": "finances"}, headers=HX_HEADERS)
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/"


def test_delete_nonexistent(client, db_engine):
    resp = client.post("/files/delete", data={"name": "nope"})
    assert resp.status_code == 404


# ---- Sanitization -----------------------------------------------------------


def test_sanitize_strips_directory_components(client, db_engine):
    resp = client.post("/files/new", data={"name": "foo/bar/baz"}, headers=HX_HEADERS)
    assert resp.status_code == 200
    with db_engine.connect() as conn:
        assert get_snapshot_id(conn, "baz") is not None
