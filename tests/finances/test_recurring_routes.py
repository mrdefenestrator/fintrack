"""Budget vs Actual's Recurring view and the Overview Needs attention card
(issue #100)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine

from fintrack.accounts.repository import add_account
from fintrack.budget.repository import get_budget_entries, get_recurring_dismissals
from fintrack.core.db import init_db
from fintrack.snapshots.repository import create_snapshot
from tests.budget.conftest import TxnSeeder, make_entry


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    today = datetime.now().astimezone().date()
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        acct = add_account(conn, sid, {"name": "Checking", "type": "checking"})
        seeder = TxnSeeder(conn, acct)
        for i in range(6):
            seeder.add(today - timedelta(days=30 * i + 2), "-11.99", "Spotify", "Fun")
            seeder.add(today - timedelta(days=30 * i + 4), "-40.00", "Gym Co", "Health")
        make_entry(conn, sid, description="Gym Co membership", amount=40)
        create_snapshot(conn, "empty")
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _sid(engine, name="home"):
    from fintrack.snapshots.repository import get_snapshot_id

    with engine.connect() as conn:
        return get_snapshot_id(conn, name)


def test_recurring_view_lists_series(client):
    body = client.get("/s/home/budget?view=recurring").get_data(as_text=True)
    assert 'data-recurring="Spotify"' in body
    assert 'data-recurring="Gym Co"' in body
    assert "1 not in budget" in body
    # Read-only outside edit mode.
    assert "data-recurring-add" not in body
    # The toggle marks the Recurring tab.
    assert 'data-view="recurring" href' in body


def test_recurring_status_filter(client):
    body = client.get("/s/home/budget?view=recurring&status=not_budgeted").get_data(
        as_text=True
    )
    assert 'data-recurring="Spotify"' in body
    assert 'data-recurring="Gym Co"' not in body


def test_plan_view_has_the_toggle(client):
    """Issue #143: Recurring is the Budget sheet's second view; the Plan /
    Recurring toggle leads both views' filter bars and keeps the month and
    edit mode."""
    body = client.get("/s/home/budget?ym=2025-01&edit=1").get_data(as_text=True)
    toggle = body.split("data-budget-views")[1].split("</div>")[0]
    assert 'data-view="plan" href' in toggle
    assert 'href="/s/home/budget?view=recurring&amp;ym=2025-01&amp;edit=1"' in toggle
    assert "recurring-table" not in body
    body = client.get("/s/home/budget?view=recurring&ym=2025-01").get_data(as_text=True)
    toggle = body.split("data-budget-views")[1].split("</div>")[0]
    assert 'href="/s/home/budget?ym=2025-01"' in toggle
    assert 'aria-current="page">Budget</a>' in body


def test_old_recurring_url_redirects(client):
    resp = client.get("/s/home/budget/actual?view=recurring&status=not_budgeted")
    assert resp.status_code == 302
    assert (
        resp.headers["Location"] == "/s/home/budget?view=recurring&status=not_budgeted"
    )


def test_edit_mode_actions(client, db_engine):
    body = client.get("/s/home/budget?view=recurring&edit=1").get_data(as_text=True)
    assert "data-recurring-add" in body  # Spotify
    assert "data-recurring-link" in body  # Gym Co → its entry by name
    assert body.count("data-recurring-dismiss") == 2

    resp = client.post(
        "/s/home/budget/recurring/add?view=recurring&edit=1",
        data={"merchant": "Spotify", "kind": "expense"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    assert "0 not in budget" not in resp.get_data(as_text=True)
    with db_engine.connect() as conn:
        entries = get_budget_entries(conn, _sid(db_engine))
    spotify = [e for e in entries if e["description"] == "Spotify"]
    assert spotify and spotify[0]["recurrence"] == "monthly"


def test_link_and_dismiss_actions(client, db_engine):
    with db_engine.connect() as conn:
        gym = get_budget_entries(conn, _sid(db_engine))[0]["_db_id"]
    resp = client.post(
        "/s/home/budget/recurring/link?view=recurring",
        data={"merchant": "Gym Co", "kind": "expense", "budget_entry_ref": gym},
    )
    assert resp.status_code == 200
    assert (
        'data-recurring="Gym Co" data-recurring-kind="expense" data-status="linked"'
        in resp.get_data(as_text=True)
    )

    resp = client.post(
        "/s/home/budget/recurring/dismiss?view=recurring",
        data={"merchant": "Spotify", "kind": "expense"},
    )
    assert 'data-recurring="Spotify"' not in resp.get_data(as_text=True)
    with db_engine.connect() as conn:
        assert get_recurring_dismissals(conn, _sid(db_engine)) == {"Spotify"}


def test_bad_action_posts_are_rejected(client):
    url = "/s/home/budget/recurring"
    assert client.post(f"{url}/add", data={"merchant": "Nobody"}).status_code == 422
    assert client.post(f"{url}/add", data={}).status_code == 422
    assert client.post(f"{url}/link", data={"merchant": "Gym Co"}).status_code == 422


def test_overview_needs_attention_card(client):
    body = client.get("/s/home").get_data(as_text=True)
    at = body.index('data-overview-card="needs_attention"')
    card = body[body.rfind("<", 0, at) : body.index("</a>", at)]
    assert "1 item" in card
    assert "Spotify: $11.99/mo, not in budget" in card
    assert "Gym Co" not in card  # matched to its entry: not news
    assert (
        'href="/s/home/budget?view=recurring&amp;status=not_budgeted'
        '&amp;status=price_change"' in card
    )


def test_overview_needs_attention_all_clear(client):
    body = client.get("/s/empty").get_data(as_text=True)
    at = body.index('data-overview-card="needs_attention"')
    assert "All clear" in body[at : body.index("</a>", at)]
