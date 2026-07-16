"""Shared fixtures for e2e Playwright tests."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_YAML = PROJECT_ROOT / "tests" / "fixtures" / "test_finances.yaml"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Flask server did not start on port {port}")


def _seed_db(db_path: Path) -> None:
    """Seed (or re-seed) the test snapshot from the fixture YAML."""
    from sqlalchemy import create_engine
    from finances.db import init_db
    from finances.repository.snapshots import delete_snapshot, get_snapshot_id
    from finances.yaml_import import import_yaml

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    init_db(engine)
    with engine.connect() as conn:
        existing = get_snapshot_id(conn, "test_finances")
        if existing is not None:
            delete_snapshot(conn, existing)
        import_yaml(conn, FIXTURE_YAML, name="test_finances")
    engine.dispose()


@pytest.fixture(scope="session")
def flask_server(tmp_path_factory):
    """Start Flask on a random port with a temp DB seeded from fixture YAML.

    Yields the base URL (e.g. ``http://127.0.0.1:54321``).
    """
    port = _free_port()
    db_dir = tmp_path_factory.mktemp("db")
    db_path = db_dir / "test_finances.db"

    _seed_db(db_path)

    env = {
        **os.environ,
        "FINANCES_DB": str(db_path),
        "FLASK_RUN_PORT": str(port),
    }

    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "web" / "app.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(port)
    except TimeoutError:
        proc.terminate()
        proc.wait(timeout=5)
        raise

    yield f"http://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="session")
def _db_path(flask_server, tmp_path_factory):
    """Return path to the DB used by the running Flask server."""
    import glob

    pattern = str(tmp_path_factory.getbasetemp() / "**" / "test_finances.db")
    matches = glob.glob(pattern, recursive=True)
    if matches:
        return Path(matches[0])
    raise FileNotFoundError("Could not find test DB file")


@pytest.fixture(autouse=True)
def _reset_data(_db_path):
    """Reset the test_finances snapshot in the DB before each test."""
    _seed_db(_db_path)
    yield


@pytest.fixture(autouse=True)
def _set_default_timeout(page):
    page.set_default_timeout(5000)


def enable_edit_mode(page):
    """Click the global lock button to enter edit mode."""
    page.locator("button[title='Enter edit mode']").click()
    page.locator("button[title='Exit edit mode']").wait_for(
        state="visible", timeout=5000
    )
