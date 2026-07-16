"""Shared fixtures for e2e Playwright tests."""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# OFX content used for data-seeding fixtures
# ---------------------------------------------------------------------------

# Four transactions in April 2026.
# Total spend: $52.75 + $18.50 + $45.00 + $15.99 = $132.24
_SEEDED_OFX = """\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM><ACCTID>9876543210</ACCTID></BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260401120000</DTPOSTED>
            <TRNAMT>-52.75</TRNAMT>
            <FITID>E2E001</FITID>
            <NAME>WHOLE FOODS MARKET</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260405120000</DTPOSTED>
            <TRNAMT>-18.50</TRNAMT>
            <FITID>E2E002</FITID>
            <NAME>CHIPOTLE MEXICAN GRILL</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260410120000</DTPOSTED>
            <TRNAMT>-45.00</TRNAMT>
            <FITID>E2E003</FITID>
            <NAME>SHELL OIL</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260415120000</DTPOSTED>
            <TRNAMT>-15.99</TRNAMT>
            <FITID>E2E004</FITID>
            <NAME>NETFLIX</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""

# A second distinct OFX file (different FITIDs and amounts) used for
# re-import / reject tests so deduplication does not suppress it.
_SEEDED_OFX_2 = """\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM><ACCTID>9876543210</ACCTID></BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260420120000</DTPOSTED>
            <TRNAMT>-29.99</TRNAMT>
            <FITID>E2E005</FITID>
            <NAME>AMAZON PRIME</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260422120000</DTPOSTED>
            <TRNAMT>-8.99</TRNAMT>
            <FITID>E2E006</FITID>
            <NAME>SPOTIFY</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""

# OFX with institution metadata — used to test the detect-account pre-fill.
_OFX_WITH_INSTITUTION = """\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <SIGNONMSGSRSV1>
    <SONRS>
      <STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
      <DTSERVER>20260401120000</DTSERVER>
      <LANGUAGE>ENG</LANGUAGE>
      <FI><ORG>Chase</ORG><FID>10898</FID></FI>
    </SONRS>
  </SIGNONMSGSRSV1>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260401120000</DTPOSTED>
            <TRNAMT>-10.00</TRNAMT>
            <FITID>INST001</FITID>
            <NAME>TEST MERCHANT</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 10.0) -> None:
    """Block until the Flask server is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Flask server did not start on port {port}")


def _start_server(port: int, db_path: Path) -> subprocess.Popen:
    """Start a Flask subprocess with a dedicated SQLite database.

    ANTHROPIC_BASE_URL is pointed at a closed local port so classification
    API calls fail instantly (ECONNREFUSED) and the classifier returns {}
    gracefully, leaving transactions as 'Uncategorized'.
    """
    env = {
        **os.environ,
        "SPENDING_DB": str(db_path),
        "SPENDING_PORT": str(port),
        "FLASK_DEBUG": "0",
        "ANTHROPIC_API_KEY": "sk-ant-test-key",
        # Port 1 is never open — instant ECONNREFUSED
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "web.app"],
        cwd=str(PROJECT_ROOT),
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
    return proc


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _form_post(url: str, fields: dict[str, str] | None = None) -> str:
    """POST application/x-www-form-urlencoded and return the response body."""
    data = urllib.parse.urlencode(fields or {}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


def _multipart_post(
    url: str,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes]],
) -> str:
    """POST multipart/form-data and return the response body."""
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n'
                f"\r\n"
                f"{value}\r\n"
            ).encode()
        )

    for field_name, (filename, content) in files.items():
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n"
            f"\r\n"
        ).encode()
        parts.append(header + content + b"\r\n")

    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read().decode()


# ---------------------------------------------------------------------------
# Session-scoped server (empty DB) — used by structure/navigation tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def flask_server(tmp_path_factory):
    """Start Flask on a random port with an empty temp SQLite database.

    Yields the base URL (e.g. ``http://127.0.0.1:54321``).
    The server is torn down after the test session.
    """
    port = _free_port()
    db_dir = tmp_path_factory.mktemp("db")
    db_path = db_dir / "test.db"
    proc = _start_server(port, db_path)
    yield f"http://127.0.0.1:{port}"
    _stop_server(proc)


# ---------------------------------------------------------------------------
# Module-scoped server with confirmed data — used by data-dependent tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def confirmed_server(tmp_path_factory):
    """Flask server pre-seeded with a confirmed import in 04/2026.

    Account: "Test Checking" (Test Bank, checking)
    Transactions (all confirmed, all Uncategorized since API is disabled):
      - WHOLE FOODS MARKET  -$52.75  2026-04-01
      - CHIPOTLE MEXICAN GRILL  -$18.50  2026-04-05
      - SHELL OIL  -$45.00  2026-04-10
      - NETFLIX  -$15.99  2026-04-15
    Grand total: $132.24
    """
    port = _free_port()
    db_dir = tmp_path_factory.mktemp("confirmed_db")
    db_path = db_dir / "test.db"
    proc = _start_server(port, db_path)
    base_url = f"http://127.0.0.1:{port}"

    # Create the account and capture its ID from the returned HTML.
    html = _form_post(
        f"{base_url}/accounts",
        {
            "acct_name": "Test Checking",
            "acct_institution": "Test Bank",
            "acct_type": "checking",
        },
    )
    m = re.search(r'<option value="(\d+)"', html)
    assert m, f"Account creation failed. Response snippet:\n{html[:500]}"
    account_id = m.group(1)

    # Upload the OFX and capture the staging import ID.
    html = _multipart_post(
        f"{base_url}/import/upload",
        {"account_id": account_id},
        {"files": ("seed.ofx", _SEEDED_OFX.encode())},
    )
    m = re.search(r'id="batch-(\d+)"', html)
    assert m, f"Import upload failed. Response snippet:\n{html[:500]}"
    import_id = m.group(1)

    # Confirm the import so transactions are visible in reports.
    _form_post(f"{base_url}/import/{import_id}/confirm")

    yield base_url
    _stop_server(proc)


# ---------------------------------------------------------------------------
# Module-scoped fresh server — used by test_import.py
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def import_server(tmp_path_factory):
    """Fresh Flask server for import workflow tests (empty database).

    Tests in test_import.py run in document order and progressively build
    state (account creation → upload → staging review → confirm/reject).
    """
    port = _free_port()
    db_dir = tmp_path_factory.mktemp("import_db")
    db_path = db_dir / "test.db"
    proc = _start_server(port, db_path)
    yield f"http://127.0.0.1:{port}"
    _stop_server(proc)


# ---------------------------------------------------------------------------
# File fixtures for upload tests
# ---------------------------------------------------------------------------


@pytest.fixture
def ofx_file(tmp_path):
    """OFX file with 4 transactions in 04/2026."""
    f = tmp_path / "import.ofx"
    f.write_bytes(_SEEDED_OFX.encode())
    return f


@pytest.fixture
def ofx_file_2(tmp_path):
    """Second distinct OFX file (different FITIDs) for reject/re-import tests."""
    f = tmp_path / "import2.ofx"
    f.write_bytes(_SEEDED_OFX_2.encode())
    return f


@pytest.fixture
def ofx_file_with_institution(tmp_path):
    """OFX file with institution metadata (Chase Checking ...7890)."""
    f = tmp_path / "chase.ofx"
    f.write_bytes(_OFX_WITH_INSTITUTION.encode())
    return f


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_default_timeout(page):
    """Use a short default timeout so test failures surface quickly."""
    page.set_default_timeout(8000)
