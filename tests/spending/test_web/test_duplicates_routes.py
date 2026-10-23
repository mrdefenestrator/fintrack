"""Staging review: resolving possible-duplicate flags (issue #82)."""

from fintrack.ledger.repository.duplicates import get_duplicate
from fintrack.snapshots.repository import get_snapshot_id


def _web_stage_pair(conn, stage_pair):
    return stage_pair(conn, get_snapshot_id(conn, "ledger"))


def test_review_shows_flag_with_original(client, conn, stage_pair):
    import_id, copy_id = _web_stage_pair(conn, stage_pair)
    html = client.get(f"/s/ledger/import/{import_id}/review").data.decode()
    assert f'data-dup-for="{copy_id}"' in html
    assert "Possible duplicate of" in html
    assert "UBER *TRIP HELP.UBER.COM" in html
    assert "same amount · 1 day apart" in html


def test_review_resolve_and_undo(client, conn, stage_pair):
    import_id, copy_id = _web_stage_pair(conn, stage_pair)
    url = f"/s/ledger/import/{import_id}/duplicate/{copy_id}"

    html = client.post(url, data={"status": "duplicate"}).data.decode()
    assert "Marked duplicate of" in html and "Undo" in html
    assert get_duplicate(conn, copy_id)["status"] == "duplicate"

    html = client.post(url, data={"status": "flagged"}).data.decode()
    assert "Possible duplicate of" in html


def test_review_resolve_bad_requests(client, conn, stage_pair):
    import_id, copy_id = _web_stage_pair(conn, stage_pair)
    base = f"/s/ledger/import/{import_id}/duplicate"
    assert client.post(f"{base}/{copy_id}", data={"status": "x"}).status_code == 400
    assert (
        client.post(f"{base}/{copy_id + 100}", data={"status": "duplicate"}).status_code
        == 404
    )
