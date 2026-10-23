import pytest
from sqlalchemy import create_engine

from fintrack.core.models import metadata


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def conn(engine):
    with engine.connect() as connection:
        yield connection


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
def write_ofx(tmp_path):
    """Factory: write an OFX statement from (fitid, yyyymmdd, amount, name) rows.

    Each call writes a distinct file (so the whole-file hash check never trips)
    and returns its path. ofxparse rejects a transaction without a FITID, so
    every row carries one.
    """
    counter = iter(range(1, 1000))

    def _write(rows: list[tuple[str, str, str, str]]):
        txns = "".join(
            f"""
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>{day}120000</DTPOSTED>
            <TRNAMT>{amount}</TRNAMT>
            <FITID>{fitid}</FITID>
            <NAME>{name}</NAME>
          </STMTTRN>"""
            for fitid, day, amount, name in rows
        )
        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="220"?>
<OFX>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <STMTRS>
        <BANKACCTFROM>
          <ACCTID>1234567890</ACCTID>
        </BANKACCTFROM>
        <BANKTRANLIST>{txns}
        </BANKTRANLIST>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>"""
        path = tmp_path / f"stmt{next(counter)}.qfx"
        path.write_text(content)
        return path

    return _write


@pytest.fixture
def stage_pair(write_ofx):
    """Factory: import two overlapping exports of one Uber charge (new bank id,
    reworded description, a day apart) into a fresh account, so the second is
    flagged as a possible duplicate. Returns (second import id, copy txn id)."""
    from sqlalchemy import select

    from fintrack.core.models import transactions
    from fintrack.ledger.importer import run_import
    from fintrack.ledger.repository.accounts import add_account

    def _stage(conn, snapshot_id=None):
        account_id = add_account(
            conn,
            name="Card",
            institution="Apple",
            account_type="checking",
            snapshot_id=snapshot_id,
        )
        run_import(
            conn,
            write_ofx([("A1", "20261001", "-56.78", "UBER *TRIP HELP.UBER.COM")]),
            account_id=account_id,
        )
        second = run_import(
            conn,
            write_ofx([("B1", "20261002", "-56.78", "Uber Trip Help.Uber.Com")]),
            account_id=account_id,
        )
        copy_id = conn.execute(
            select(transactions.c.id).where(
                transactions.c.import_id == second["import_id"]
            )
        ).scalar_one()
        return second["import_id"], copy_id

    return _stage
