"""Cross-domain e2e: one snapshot drives both the ledger and net-worth pages.

Walks the unified flow from the Phase 3 design spec: pick a snapshot, create
an account from the import page, upload + confirm an OFX, see the transaction
in the ledger — then see the same account in the net-worth accounts grid.
"""

import pytest

from tests.e2e.ledger.conftest import (
    _SEEDED_OFX,
    _create_ledger_snapshot,
    _form_post,
    _free_port,
    _multipart_post,
    _start_server,
    _stop_server,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def unified_server(tmp_path_factory):
    port = _free_port()
    db_path = tmp_path_factory.mktemp("unified_db") / "test.db"
    proc = _start_server(port, db_path)
    base_url = f"http://127.0.0.1:{port}"
    _create_ledger_snapshot(base_url)
    yield base_url
    _stop_server(proc)


def test_snapshot_picker_lists_created_snapshot(page, unified_server):
    page.goto(unified_server)
    picker = page.locator("[data-file-picker-dropdown]")
    assert picker.is_visible()
    assert "ledger" in picker.inner_text()


def test_import_confirm_transaction_and_networth_account(page, unified_server):
    import re

    # Seed over HTTP: account + confirmed OFX import
    html = _form_post(
        f"{unified_server}/s/ledger/import/accounts",
        {
            "acct_name": "Flow Checking",
            "acct_institution": "Flow Bank",
            "acct_type": "checking",
        },
    )
    m = re.search(r'<option value="(\d+)"', html)
    assert m
    html = _multipart_post(
        f"{unified_server}/s/ledger/import/upload",
        {"account_id": m.group(1)},
        {"files": ("flow.ofx", _SEEDED_OFX.encode())},
    )
    m = re.search(r'id="batch-(\d+)"', html)
    assert m
    _form_post(f"{unified_server}/s/ledger/import/{m.group(1)}/confirm")

    # Ledger side: the confirmed transaction is visible
    page.goto(f"{unified_server}/s/ledger/transactions?year=2026&month=4")
    assert page.locator("text=WHOLE FOODS MARKET").first.is_visible()

    # Net-worth side: the same account appears in the accounts grid
    page.goto(f"{unified_server}/s/ledger/accounts")
    assert page.locator("td", has_text="Flow Checking").first.is_visible()

    # Status dashboard renders the key-numbers rollup for the snapshot
    page.goto(f"{unified_server}/s/ledger/status")
    body = page.locator("tbody").inner_text()
    assert "Accounts" in body and "Total" in body
