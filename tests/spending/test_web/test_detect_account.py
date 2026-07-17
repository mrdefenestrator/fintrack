import io

import pytest

from fintrack.ledger.repository.accounts import add_account
from fintrack.snapshots.repository import get_snapshot_id


@pytest.fixture
def snapshot_id(conn):
    """The 'ledger' snapshot id the conftest `app` fixture creates. add_account
    defaults to the separate 'default' snapshot, so tests that seed accounts
    which detect-account (scoped to 'ledger') must find have to pass this
    explicitly."""
    return get_snapshot_id(conn, "ledger")


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
        "/s/ledger/import/detect-account",
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
        "/s/ledger/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "No accounts yet" in html


def test_detect_account_csv_returns_empty_form(client):
    csv_bytes = b"Transaction Date,Description,Amount\n01/15/2024,COFFEE,-5.00\n"
    response = client.post(
        "/s/ledger/import/detect-account",
        data={"files": (io.BytesIO(csv_bytes), "test.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    # form rendered without error
    assert b"account-panel" in response.data


def test_detect_account_corrupt_file_degrades_gracefully(client):
    response = client.post(
        "/s/ledger/import/detect-account",
        data={"files": (io.BytesIO(b"not valid ofx"), "test.ofx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert b"account-panel" in response.data


# ---------------------------------------------------------------------------
# Auto-detect must not clobber a manual selection (issue 10)
# ---------------------------------------------------------------------------


def _is_selected(html: str, account_id: int) -> bool:
    return f'value="{account_id}" selected' in html


def test_detect_account_matches_confident_single_candidate(
    client, conn, snapshot_id, ofx_with_meta_bytes
):
    """Institution + type + last-4-in-name match wins over a stale prior pick."""
    stale_id = add_account(
        conn,
        name="Wells Checking",
        institution="Wells Fargo",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    match_id = add_account(
        conn,
        name="Chase Checking ...7890",
        institution="Chase",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    response = client.post(
        "/s/ledger/import/detect-account",
        data={
            "files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx"),
            "account_id": str(stale_id),
        },
        content_type="multipart/form-data",
    )
    html = response.data.decode()
    assert _is_selected(html, match_id)
    assert not _is_selected(html, stale_id)


def test_detect_account_ambiguous_candidates_preserve_prior_selection(
    client, conn, snapshot_id, ofx_with_meta_bytes
):
    """Two same-institution/type accounts with no last-4 hint in either name
    is not confident enough to auto-select; the user's existing pick sticks."""
    prev_id = add_account(
        conn,
        name="Chase Checking A",
        institution="Chase",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    add_account(
        conn,
        name="Chase Checking B",
        institution="Chase",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    response = client.post(
        "/s/ledger/import/detect-account",
        data={
            "files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx"),
            "account_id": str(prev_id),
        },
        content_type="multipart/form-data",
    )
    html = response.data.decode()
    assert _is_selected(html, prev_id)


def test_detect_account_no_institution_match_preserves_prior_selection(
    client, conn, snapshot_id, ofx_with_meta_bytes
):
    """When no account matches the OFX institution/type at all, the user's
    existing selection is kept rather than being reset to 'Select account...'."""
    prev_id = add_account(
        conn,
        name="Wells Checking",
        institution="Wells Fargo",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    response = client.post(
        "/s/ledger/import/detect-account",
        data={
            "files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx"),
            "account_id": str(prev_id),
        },
        content_type="multipart/form-data",
    )
    html = response.data.decode()
    assert _is_selected(html, prev_id)


def test_detect_account_no_match_no_prior_selection_stays_unselected(
    client, conn, snapshot_id, ofx_with_meta_bytes
):
    """With no confident match and nothing previously selected, the dropdown
    is left on 'Select account...' rather than guessing."""
    add_account(
        conn,
        name="Wells Checking",
        institution="Wells Fargo",
        account_type="checking",
        snapshot_id=snapshot_id,
    )
    response = client.post(
        "/s/ledger/import/detect-account",
        data={"files": (io.BytesIO(ofx_with_meta_bytes), "test.ofx")},
        content_type="multipart/form-data",
    )
    html = response.data.decode()
    assert "selected" not in html.split("Select account")[1].split("</select>")[0]
