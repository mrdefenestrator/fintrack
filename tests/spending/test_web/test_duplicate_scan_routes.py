"""Web: scan button and resolving flags on the Transactions sheet (#82)."""

import pytest
from sqlalchemy import delete, select

from fintrack.core.models import transaction_duplicates, transactions
from fintrack.ledger.repository.duplicates import get_duplicate
from fintrack.ledger.repository.imports import confirm_import
from fintrack.snapshots.repository import get_snapshot_id


def _confirmed_pair(conn, stage_pair):
    _, copy_id = stage_pair(conn, get_snapshot_id(conn, "ledger"))
    for iid in conn.execute(select(transactions.c.import_id).distinct()).scalars():
        confirm_import(conn, iid)
    return copy_id


@pytest.fixture
def legacy_web_copies(conn, stage_pair):
    """A confirmed copy imported before flagging existed (its flag wiped)."""
    copy_id = _confirmed_pair(conn, stage_pair)
    conn.execute(delete(transaction_duplicates))
    conn.commit()
    return copy_id


def test_scan_button_reports_and_links(client, conn, legacy_web_copies):
    html = client.post("/s/ledger/import/scan").data.decode()
    assert "Flagged 1 possible duplicate." in html
    assert "status=possible_duplicate" in html
    html = client.post("/s/ledger/import/scan").data.decode()
    assert "No new possible duplicates found." in html


def test_sheet_filter_shows_badge_and_actions(client, conn, stage_pair):
    copy_id = _confirmed_pair(conn, stage_pair)
    html = client.get(
        "/s/ledger/transactions?all_months=true&status=possible_duplicate"
    ).data.decode()
    assert f'id="txn-row-{copy_id}"' in html
    assert "Possible duplicate of" in html and "UBER *TRIP HELP.UBER.COM" in html
    assert "Not duplicate" in html


def test_sheet_resolve_rerenders_row_in_its_mode(client, conn, stage_pair):
    copy_id = _confirmed_pair(conn, stage_pair)
    url = f"/s/ledger/transactions/{copy_id}/duplicate"

    html = client.post(url, data={"status": "duplicate"}).data.decode()
    assert get_duplicate(conn, copy_id)["status"] == "duplicate"
    assert f'id="txn-row-{copy_id}"' in html and "Undo" in html
    assert "/cell?field=" not in html  # view mode

    html = client.post(url, data={"status": "flagged", "edit": "1"}).data.decode()
    assert "Possible duplicate of" in html
    assert "/cell?field=" in html  # edit mode

    marked = "/s/ledger/transactions?all_months=true&status=duplicate"
    client.post(url, data={"status": "duplicate"})
    assert f'id="txn-row-{copy_id}"' in client.get(marked).data.decode()
    everything = client.get("/s/ledger/transactions?all_months=true").data.decode()
    assert f'id="txn-row-{copy_id}"' not in everything


def test_sheet_resolve_bad_requests(client, conn, stage_pair):
    copy_id = _confirmed_pair(conn, stage_pair)
    url = f"/s/ledger/transactions/{copy_id}/duplicate"
    assert client.post(url, data={"status": "nope"}).status_code == 400
    missing = f"/s/ledger/transactions/{copy_id + 99}/duplicate"
    assert client.post(missing, data={"status": "duplicate"}).status_code == 404
