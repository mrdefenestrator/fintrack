import io
from unittest.mock import patch

from fintrack.ledger.repository.accounts import add_account

_SIMPLE_OFX = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM><ACCTID>1234567890</ACCTID></BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260401120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>T001</FITID>
            <NAME>WHOLE FOODS</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""


def test_upload_shows_classified_count(client, conn):
    acct_id = add_account(
        conn, name="Test", institution="Test Bank", account_type="checking"
    )
    with patch("web.routes.imports.classify_and_cache", return_value=(3, None)):
        response = client.post(
            "/s/ledger/import/upload",
            data={
                "account_id": str(acct_id),
                "files": (io.BytesIO(_SIMPLE_OFX), "test.ofx"),
            },
            content_type="multipart/form-data",
        )
    html = response.data.decode()
    assert "3 merchants auto-classified" in html


def test_upload_no_classified_count_when_zero(client, conn):
    acct_id = add_account(
        conn, name="Test", institution="Test Bank", account_type="checking"
    )
    with patch("web.routes.imports.classify_and_cache", return_value=(0, None)):
        response = client.post(
            "/s/ledger/import/upload",
            data={
                "account_id": str(acct_id),
                "files": (io.BytesIO(_SIMPLE_OFX), "test.ofx"),
            },
            content_type="multipart/form-data",
        )
    html = response.data.decode()
    assert "auto-classified" not in html


def test_upload_without_file_shows_error_as_422(client, conn):
    """A request missing its file body (e.g. iPadOS Safari losing access to
    the picked file, #74) re-renders the page with a visible error."""
    acct_id = add_account(
        conn, name="Test", institution="Test Bank", account_type="checking"
    )
    response = client.post(
        "/s/ledger/import/upload",
        data={"account_id": str(acct_id)},
        content_type="multipart/form-data",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 422
    html = response.data.decode()
    assert 'id="import-error"' in html
    assert "didn&#39;t include a statement file" in html
    assert "Import Statements" in html


def test_upload_without_account_names_the_missing_field(client, conn):
    response = client.post(
        "/s/ledger/import/upload",
        data={"files": (io.BytesIO(_SIMPLE_OFX), "test.ofx")},
        content_type="multipart/form-data",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 422
    html = response.data.decode()
    assert "didn&#39;t include an account" in html
    assert "statement file" not in html


def test_upload_empty_file_reports_unreadable(client, conn):
    acct_id = add_account(
        conn, name="Test", institution="Test Bank", account_type="checking"
    )
    response = client.post(
        "/s/ledger/import/upload",
        data={
            "account_id": str(acct_id),
            "files": (io.BytesIO(b""), "test.ofx"),
        },
        content_type="multipart/form-data",
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "test.ofx: file arrived empty" in html
