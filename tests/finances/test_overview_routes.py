"""Overview page (issue #105): the snapshot landing page — read-only cards that
link out to the view behind each number."""

import re

import pytest
from sqlalchemy import create_engine

from fintrack.accounts.repository import add_account
from fintrack.core.db import init_db
from fintrack.snapshots.repository import create_snapshot


@pytest.fixture()
def client():
    from web.app import create_app

    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        # Below its minimum from the first month: one projection warning.
        add_account(
            conn,
            sid,
            {
                "name": "Checking",
                "type": "checking",
                "institution": "Chase",
                "balance": 1000,
                "minimum_balance": 2000,
            },
        )
        create_snapshot(conn, "empty")

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = engine
    with app.test_client() as c:
        yield c


def _card(html: str, key: str) -> str:
    """A card's markup: from its opening tag to the next card (or page end)."""
    marker = f'data-overview-card="{key}"'
    assert marker in html, f"no {key} card"
    at = html.index(marker)
    start = html.rfind("<", 0, at)
    nxt = html.find("data-overview-card=", at + len(marker))
    return html[start : nxt if nxt != -1 else len(html)]


def test_snapshot_root_is_the_overview(client):
    resp = client.get("/s/home")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<title>Overview — fintrack</title>" in html
    for key in ("net_worth_liquid", "projection_warnings"):
        _card(html, key)


@pytest.mark.parametrize("path", ["/s/home", "/s/home/"])
def test_both_root_forms_render_without_a_redirect(client, path):
    """No slash redirect: the PR-preview Lambda strips the trailing slash, so
    a /s/home -> /s/home/ redirect would loop forever there."""
    resp = client.get(path)
    assert resp.status_code == 200
    assert "<title>Overview — fintrack</title>" in resp.get_data(as_text=True)


def test_unknown_snapshot_404s(client):
    assert client.get("/s/nope").status_code == 404


def test_net_worth_and_liquid_card(client):
    """One card, whole dollars, and the whole card links to Projections."""
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "net_worth_liquid")
    assert card.startswith('<a href="/s/home/projections?months=12"')
    for testid in ("net-worth", "liquid"):
        figure = re.search(
            rf'data-figure="{testid}".*?data-change.*?</div>', card, re.DOTALL
        ).group(0)
        assert "$1,000<" in figure  # now, no cents
        assert "No change" in figure
    assert "Projected change by " in card
    assert "$1,000.00" not in card


def test_warnings_card_is_one_link_to_projections(client):
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "projection_warnings")
    assert "1 account dips below its minimum" in card
    assert "Low point" in card
    assert "Chase Checking</span>: under $2,000 min from " in card
    assert "All clear" not in card
    # One link: the card itself, to plain Projections (no month).
    assert card.startswith('<a href="/s/home/projections?months=12"')
    assert card.count("<a ") == 1
    assert "month=" not in card


def test_empty_snapshot(client):
    html = client.get("/s/empty").get_data(as_text=True)
    assert "data-overview-empty" in html
    assert 'href="/s/empty/holdings"' in html
    card = _card(html, "projection_warnings")
    assert "All clear" in card
    assert "data-warnings" not in card


def test_overview_is_the_first_nav_item(client):
    html = client.get("/s/home/holdings").get_data(as_text=True)
    nav = re.search(r"<nav aria-label=\"Sections\".*?</nav>", html, re.DOTALL)
    links = re.findall(r"<a href=\"([^\"]+)\"[^>]*>([^<]+)</a>", nav.group(0))
    assert links[0] == ("/s/home", "Overview")


def test_overview_marks_itself_active(client):
    html = client.get("/s/home").get_data(as_text=True)
    assert re.search(r'aria-current="page">Overview</a>', html)


def test_legacy_status_url_lands_on_overview(client):
    resp = client.get("/s/home/status")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/s/home")


def test_projections_keeps_a_one_line_summary(client):
    """Projections' cards and warnings moved to Overview; one line remains."""
    html = client.get("/s/home/projections").get_data(as_text=True)
    assert 'data-summary="line"' in html
    assert 'data-summary="liquid"' in html
    assert 'data-summary="net-worth"' in html
    assert 'data-summary="warnings"' not in html
    assert 'data-summary="lowest"' not in html


def test_cash_flow_card_links_to_trailing_12_trends(client):
    """The Cash flow card (#99) opens Trends on the window its average covers;
    with no transactions it says so instead of showing a rate."""
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "cash_flow")
    assert card.startswith('<a href="/s/home/trends?period=trailing12"')
    assert "Cash flow" in card
    assert "saved in " in card
    assert "No transactions yet this month" in card
    assert "No completed months in the past 12 yet" in card


def test_needs_attention_opens_holdings_when_it_leads_with_stale_data(client):
    from datetime import datetime, timedelta

    engine = client.application.config["engine"]
    today = datetime.now().astimezone().date()
    with engine.connect() as conn:
        add_account(
            conn,
            1,
            {
                "name": "Old Savings",
                "type": "savings",
                "balance": 5,
                "asOfDate": (today - timedelta(days=41)).isoformat(),
            },
        )
    card = _card(client.get("/s/home").get_data(as_text=True), "needs_attention")
    assert "Old Savings: last updated 41 days ago" in card
    assert 'href="/s/home/holdings"' in card
