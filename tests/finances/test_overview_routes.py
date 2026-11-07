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
    m = re.search(rf'<section data-overview-card="{key}".*?</section>', html, re.DOTALL)
    assert m, f"no {key} card"
    return m.group(0)


def test_snapshot_root_is_the_overview(client):
    resp = client.get("/s/home/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<title>Overview — fintrack</title>" in html
    for key in ("net_worth", "liquid", "projection_warnings"):
        _card(html, key)


def test_snapshot_root_without_slash_redirects(client):
    resp = client.get("/s/home")
    assert resp.status_code in (301, 308)
    assert resp.headers["Location"].endswith("/s/home/")


def test_unknown_snapshot_404s(client):
    assert client.get("/s/nope/").status_code == 404


def test_net_worth_and_liquid_cards(client):
    html = client.get("/s/home/").get_data(as_text=True)
    for key in ("net_worth", "liquid"):
        card = _card(html, key)
        assert "$1,000.00" in card  # now
        assert "projected over the next 12 months" in card
        assert 'href="/s/home/projections?months=12"' in card


def test_warnings_card_links_each_item_to_its_month(client):
    html = client.get("/s/home/").get_data(as_text=True)
    card = _card(html, "projection_warnings")
    assert "Lowest liquid" in card
    assert "drops below its $2,000.00 minimum" in card
    # Checking is below its minimum from month 0 (the current month).
    assert 'href="/s/home/projections?months=12&amp;month=0"' in card
    assert "data-no-warnings" not in card


def test_empty_snapshot(client):
    html = client.get("/s/empty/").get_data(as_text=True)
    assert "data-overview-empty" in html
    assert 'href="/s/empty/holdings"' in html
    card = _card(html, "projection_warnings")
    assert "data-no-warnings" in card


def test_overview_is_the_first_nav_item(client):
    html = client.get("/s/home/holdings").get_data(as_text=True)
    nav = re.search(r"<nav aria-label=\"Sections\".*?</nav>", html, re.DOTALL)
    links = re.findall(r"<a href=\"([^\"]+)\"[^>]*>([^<]+)</a>", nav.group(0))
    assert links[0] == ("/s/home/", "Overview")


def test_overview_marks_itself_active(client):
    html = client.get("/s/home/").get_data(as_text=True)
    assert re.search(r'aria-current="page">Overview</a>', html)


def test_legacy_status_url_lands_on_overview(client):
    resp = client.get("/s/home/status")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/s/home/")


def test_projections_keeps_a_one_line_summary(client):
    """Projections' cards and warnings moved to Overview; one line remains."""
    html = client.get("/s/home/projections").get_data(as_text=True)
    assert 'data-summary="line"' in html
    assert 'data-summary="liquid"' in html
    assert 'data-summary="net-worth"' in html
    assert 'data-summary="warnings"' not in html
    assert 'data-summary="lowest"' not in html
