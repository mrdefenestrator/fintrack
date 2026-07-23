"""Cross-domain e2e: one snapshot drives both the ledger and net-worth pages.

Walks the unified flow from the Phase 3 design spec: pick a snapshot, create
an account from the import page, upload + confirm an OFX, see the transaction
in the ledger — then see the same account in the Holdings grid.
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

    # Net-worth side: the same account appears in the Holdings grid (Cash group)
    page.goto(f"{unified_server}/s/ledger/holdings")
    assert page.locator("td", has_text="Flow Checking").first.is_visible()

    # Legacy status URL redirects to Holdings (Accounts page was retired)
    page.goto(f"{unified_server}/s/ledger/status")
    page.wait_for_url("**/holdings**")
    assert page.locator("td", has_text="Flow Checking").first.is_visible()


_OFX_WITH_LEDGERBAL = """\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM><ACCTID>555000111</ACCTID></BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260610120000</DTPOSTED>
            <TRNAMT>-40.00</TRNAMT>
            <FITID>BAL001</FITID>
            <NAME>HARDWARE STORE</NAME>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL>
          <BALAMT>1234.56</BALAMT>
          <DTASOF>20260630120000</DTASOF>
        </LEDGERBAL>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""


def test_statement_balance_feeds_holdings(page, unified_server):
    """Confirming a statement with a LEDGERBAL updates the account's balance and
    as-of date shown in the Holdings Cash group; a manual edit then changes it.

    (The balance-history data layer is covered by unit tests; the per-row
    sparkline was an Accounts-page feature, retired with that page.)"""
    import re

    html = _form_post(
        f"{unified_server}/s/ledger/import/accounts",
        {
            "acct_name": "Balance Checking",
            "acct_institution": "Bal Bank",
            "acct_type": "checking",
        },
    )
    m = re.search(r'<option value="(\d+)" selected', html)
    assert m, f"No selected account option. Snippet:\n{html[:500]}"
    account_id = m.group(1)
    html = _multipart_post(
        f"{unified_server}/s/ledger/import/upload",
        {"account_id": account_id},
        {"files": ("bal.ofx", _OFX_WITH_LEDGERBAL.encode())},
    )
    m = re.search(r'id="batch-(\d+)"', html)
    assert m
    _form_post(f"{unified_server}/s/ledger/import/{m.group(1)}/confirm")

    page.goto(f"{unified_server}/s/ledger/holdings")
    row = page.locator(f"#holding-row-account-{account_id}")
    assert "1,234.56" in row.inner_text()
    assert "2026-06-30" in row.inner_text()  # LEDGERBAL as-of date

    # Manual balance edit (Amount cell) updates the value
    page.goto(f"{unified_server}/s/ledger/holdings?edit=1")
    row = page.locator(f"#holding-row-account-{account_id}")
    row.locator("td").nth(3).click()  # Amount is the 4th cell
    inp = page.locator("#holdings-table input[name='value']")
    inp.wait_for(state="visible")
    inp.click()
    inp.fill("1300.00")
    with page.expect_response(
        lambda r: "/holdings/update/account/" in r.url and r.request.method == "POST",
        timeout=5000,
    ):
        page.locator("#holdings-table tr.total-row td").first.click()
    page.wait_for_selector(f"#holding-row-account-{account_id}")
    row = page.locator(f"#holding-row-account-{account_id}")
    assert "1,300.00" in row.inner_text()
