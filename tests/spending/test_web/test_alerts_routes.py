"""Spending alerts on the web (issue #101): the Overview card, Transactions
markers + the Flagged filter + Dismiss, and the import confirmation."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import insert, select

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import add_budget_entry
from fintrack.core.models import holdings, imports, transactions
from fintrack.snapshots.repository import get_snapshot_id
from tests.projections.conftest import seed_transactions


def _today() -> date:
    return datetime.now().astimezone().date()


@pytest.fixture
def alerted(conn):
    """A large charge and a new merchant in the last few days, plus a quiet
    charge. Returns {name: transaction id}."""
    sid = get_snapshot_id(conn, "ledger")
    account = add_account(conn, sid, {"name": "Card", "type": "credit_card"})
    # A roomy budget keeps Shopping from running hot whatever today's date.
    add_budget_entry(
        conn,
        sid,
        {
            "description": "Shopping",
            "kind": "expense",
            "amount": 5000,
            "recurrence": "monthly",
            "category": "Shopping",
        },
    )
    today = _today()
    seed_transactions(
        conn,
        account,
        [
            (today - timedelta(days=120), Decimal(-50), "HARDWARE", "Shopping"),
            (today - timedelta(days=90), Decimal(-50), "HARDWARE", "Shopping"),
            (today - timedelta(days=60), Decimal(-50), "HARDWARE", "Shopping"),
            (today - timedelta(days=2), Decimal(-200), "HARDWARE", "Shopping"),
            (today - timedelta(days=1), Decimal(-80), "STRANGER", "Shopping"),
            (today - timedelta(days=1), Decimal(-45), "HARDWARE", "Shopping"),
        ],
    )
    ids = conn.execute(select(transactions.c.id).order_by(transactions.c.id)).scalars()
    ids = list(ids)
    return {"large": ids[3], "new": ids[4], "quiet": ids[5]}


def _card(html: str) -> str:
    at = html.index('data-overview-card="needs_attention"')
    start = html.rfind("<a", 0, at)
    return html[start : html.index("</a>", at)]


def test_overview_card_lists_alerts_and_links_to_flagged(client, alerted):
    card = _card(client.get("/s/ledger").get_data(as_text=True))
    assert card.startswith(
        '<a href="/s/ledger/transactions?all_months=true&amp;status=flagged"'
    )
    assert "2 things to look at" in card
    assert "Large charge: HARDWARE $200" in card
    assert "New merchant: STRANGER $80" in card
    assert card.count("<a ") == 1  # the whole card is the one link


def test_overview_card_all_clear(client):
    card = _card(client.get("/s/ledger").get_data(as_text=True))
    assert "All clear" in card
    assert "No unusual spending in the last 30 days" in card


def test_transactions_marks_flagged_rows(client, alerted):
    html = client.get("/s/ledger/transactions?all_months=true").get_data(as_text=True)
    assert f'id="txn-row-{alerted["large"]}"' in html
    assert html.count('data-alert="large_charge"') == 1
    assert html.count('data-alert="new_merchant"') == 1
    assert "Large charge · usually $50" in html
    assert "New merchant · first charge here" in html


def test_flagged_filter_shows_only_alerted_rows(client, alerted):
    html = client.get("/s/ledger/transactions?all_months=true&status=flagged").get_data(
        as_text=True
    )
    assert f'id="txn-row-{alerted["large"]}"' in html
    assert f'id="txn-row-{alerted["new"]}"' in html
    assert f'id="txn-row-{alerted["quiet"]}"' not in html
    assert 'value="flagged"' in html  # the Status filter option


def test_dismiss_hides_the_alert_everywhere_and_undo_brings_it_back(client, alerted):
    txn = alerted["large"]
    url = f"/s/ledger/transactions/{txn}/alert"
    row = client.post(url, data={"action": "dismiss"}).get_data(as_text=True)
    assert "Alert dismissed" in row and "Undo" in row
    assert "data-alert=" not in row

    flagged = client.get(
        "/s/ledger/transactions?all_months=true&status=flagged"
    ).get_data(as_text=True)
    assert f'id="txn-row-{txn}"' not in flagged
    card = _card(client.get("/s/ledger").get_data(as_text=True))
    assert "1 thing to look at" in card
    assert "HARDWARE" not in card

    row = client.post(url, data={"action": "undo"}).get_data(as_text=True)
    assert 'data-alert="large_charge"' in row


def test_alert_action_bad_requests(client, alerted):
    base = "/s/ledger/transactions"
    assert client.post(f"{base}/{alerted['large']}/alert", data={}).status_code == 400
    assert (
        client.post(f"{base}/99999/alert", data={"action": "dismiss"}).status_code
        == 404
    )


def test_import_confirmation_counts_its_alerts(client, conn, alerted):
    """Confirming an import says how many of its rows need a look."""
    row = conn.execute(
        select(transactions.c.account_id, transactions.c.import_id).where(
            transactions.c.id == alerted["large"]
        )
    ).first()
    group = conn.execute(
        select(holdings.c.group_key).where(holdings.c.id == row.account_id)
    ).scalar()
    staged = conn.execute(
        insert(imports).values(
            account_id=row.account_id,
            holding_group=group,
            filename="new.ofx",
            file_hash="abc",
            status="staging",
        )
    ).inserted_primary_key[0]
    conn.execute(
        insert(transactions).values(
            import_id=staged,
            account_id=row.account_id,
            date=_today(),
            amount=Decimal(-300),
            raw_description="HARDWARE",
            normalized_merchant="HARDWARE",
            fingerprint="fp-new",
        )
    )
    conn.commit()
    html = client.post(f"/s/ledger/import/{staged}/confirm").get_data(as_text=True)
    assert "1 thing to look at in this import" in html
    assert "status=flagged" in html
