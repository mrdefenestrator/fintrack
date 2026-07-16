import io
import pytest


@pytest.fixture
def ofx_with_meta_bytes():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <SIGNONMSGSRSV1>
    <SONRS>
      <STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
      <DTSERVER>20240115120000</DTSERVER>
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
            <DTPOSTED>20240115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>20240115001</FITID>
            <NAME>WHOLE FOODS</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    return content.encode()


def test_detect_account_ofx_prefills_institution(client, ofx_with_meta_bytes):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "Chase" in html


def test_detect_account_ofx_no_accounts_shows_expanded_form(
    client, ofx_with_meta_bytes
):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "No accounts yet" in html


def test_detect_account_csv_returns_empty_form(client):
    csv_bytes = b"Transaction Date,Description,Amount\n01/15/2024,COFFEE,-5.00\n"
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(csv_bytes), "test.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    # form rendered without error
    assert b"account-panel" in response.data


def test_detect_account_corrupt_file_degrades_gracefully(client):
    response = client.post(
        "/import/detect-account",
        data={"files": (io.BytesIO(b"not valid ofx"), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"account-panel" in response.data
