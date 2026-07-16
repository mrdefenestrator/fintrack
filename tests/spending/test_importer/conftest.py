import pytest


@pytest.fixture
def sample_ofx(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20240115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>20240115001</FITID>
            <NAME>WHOLE FOODS MARKET #10234</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20240116120000</DTPOSTED>
            <TRNAMT>1500.00</TRNAMT>
            <FITID>20240116001</FITID>
            <NAME>DIRECT DEPOSIT ACME CORP</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_ofx_with_meta(tmp_path):
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
    path = tmp_path / "test_meta.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_ofx_with_balances(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <CURDEF>USD</CURDEF>
        <BANKACCTFROM>
          <BANKID>021000021</BANKID>
          <ACCTID>9876543210</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20260101</DTSTART>
          <DTEND>20260131</DTEND>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260115120000</DTPOSTED>
            <TRNAMT>-42.50</TRNAMT>
            <FITID>20260115001</FITID>
            <NAME>WHOLE FOODS</NAME>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL>
          <BALAMT>1234.56</BALAMT>
          <DTASOF>20260131120000</DTASOF>
        </LEDGERBAL>
        <AVAILBAL>
          <BALAMT>1184.56</BALAMT>
          <DTASOF>20260131120000</DTASOF>
        </AVAILBAL>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test_balances.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_ofx_no_balances(tmp_path):
    content = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1111</ACCTID>
          <ACCTTYPE>CHECKING</ACCTTYPE>
        </BANKACCTFROM>
        <BANKTRANLIST>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20260115120000</DTPOSTED>
            <TRNAMT>-10.00</TRNAMT>
            <FITID>001</FITID>
            <NAME>COFFEE</NAME>
          </STMTTRN>
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
    path = tmp_path / "test_no_balances.ofx"
    path.write_text(content)
    return path


@pytest.fixture
def sample_venmo_csv(tmp_path):
    content = (
        "Account Statement - (@Test-User) ,,,,,,,,,,,,,,,,,,,,,\n"
        "Account Activity,,,,,,,,,,,,,,,,,,,,,\n"
        ",ID,Datetime,Type,Status,Note,From,To,Amount (total),Amount (tip),Amount (tax),Amount (fee),Tax Rate,Tax Exempt,Funding Source,Destination,Beginning Balance,Ending Balance,Statement Period Venmo Fees,Terminal Location,Year to Date Venmo Fees,Disclaimer\n"
        ",,,,,,,,,,,,,,,,$0.00,,,,,\n"
        ",1001,2026-04-03T16:03:48,Payment,Complete,March phone bill,Alice Smith,Test User,+ $46.74,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",1002,2026-04-10T20:53:20,Payment,Complete,Dinner,Bob Jones,Test User,+ $20.00,,0,,0,,,Venmo balance,,,,Venmo,,\n"
        ",1003,2026-04-10T21:07:55,Standard Transfer,Issued,,,,- $66.74,,,,,,,US BANK NA *1234,,,,Venmo,,\n"
        ',,,,,,,,,,,,,,,,,$0.00,$0.00,,$0.00,"Disclaimer text"\n'
    )
    path = tmp_path / "venmo.csv"
    path.write_text(content)
    return path


@pytest.fixture
def sample_csv(tmp_path):
    content = """Transaction Date,Post Date,Description,Category,Type,Amount
01/15/2024,01/16/2024,WHOLE FOODS MARKET #10234,Groceries,Sale,-42.50
01/16/2024,01/17/2024,DIRECT DEPOSIT ACME CORP,Income,Payment,1500.00
01/16/2024,01/17/2024,COFFEE SHOP,Food & Drink,Sale,-5.00
01/16/2024,01/17/2024,COFFEE SHOP,Food & Drink,Sale,-5.00
"""
    path = tmp_path / "test.csv"
    path.write_text(content)
    return path
