"""Fixtures for the generic sheet-renderer e2e suite.

Starts a Flask server with the test-only kitchen-sink sheet blueprint enabled
(FINTRACK_SHEET_DEMO=1) so the framework's interactive behaviour can be
exercised in a browser independent of any real table's data.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Flask server did not start on port {port}")


@pytest.fixture(scope="session")
def demo_server(tmp_path_factory):
    port = _free_port()
    db_path = tmp_path_factory.mktemp("db") / "demo.db"
    env = {
        **os.environ,
        "FINTRACK_DB": str(db_path),
        "FINTRACK_PORT": str(port),
        "FLASK_DEBUG": "0",
        "FINTRACK_SHEET_DEMO": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "web.app"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


@pytest.fixture(autouse=True)
def _reset_demo(demo_server, page):
    """Reseed the in-memory demo store before each test."""
    page.request.post(f"{demo_server}/_sheet_demo/reset")
    yield


@pytest.fixture(autouse=True)
def _set_default_timeout(page):
    page.set_default_timeout(5000)
