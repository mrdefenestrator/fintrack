import io
from unittest.mock import patch

from spending.repository.accounts import add_account

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
            "/import/upload",
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
            "/import/upload",
            data={
                "account_id": str(acct_id),
                "files": (io.BytesIO(_SIMPLE_OFX), "test.ofx"),
            },
            content_type="multipart/form-data",
        )
    html = response.data.decode()
    assert "auto-classified" not in html
