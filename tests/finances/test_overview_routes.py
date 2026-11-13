"""Overview page (issue #105): the snapshot landing page — read-only cards that
link out to the view behind each number."""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from fintrack.accounts.balance_history import record_balance
from fintrack.accounts.repository import add_account, get_accounts
from fintrack.core.db import init_db
from fintrack.projections.timeline import shift_month
from fintrack.snapshots.repository import create_snapshot, get_snapshot_id


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
    """One card, whole dollars, and the whole card links to Projections. With
    no balances recorded a year back, there's no past change to show."""
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "net_worth_liquid")
    assert card.startswith('<a href="/s/home/projections?months=12"')
    for testid in ("net-worth", "liquid"):
        figure = re.search(
            rf'data-figure="{testid}".*?data-change.*?</div>', card, re.DOTALL
        ).group(0)
        assert "$1,000<" in figure  # now, no cents
        assert "No history back to " in figure
        assert "No change" in figure
        assert ">by " in figure
    assert "$1,000.00" not in card


def test_net_worth_card_shows_the_past_year_and_opens_the_timeline(client):
    """A balance recorded a year back gives each figure its change since that
    month-end, and the card opens Projections over that past year and the
    next 12 months."""
    today = datetime.now().astimezone().date()
    then = shift_month((today.year, today.month), -12)
    with client.application.config["engine"].connect() as conn:  # it commits
        checking = get_accounts(conn, get_snapshot_id(conn, "home"))[0]["id"]
        record_balance(
            conn,
            account_id=checking,
            balance=Decimal(400),
            as_of=date(then[0], then[1], 1),
        )
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "net_worth_liquid")
    key = f"{then[0]:04d}-{then[1]:02d}"
    assert card.startswith(f'<a href="/s/home/projections?months=24&amp;from={key}"')
    label = date(then[0], then[1], 1).strftime("%b %Y")
    for testid in ("net-worth", "liquid"):
        past = re.search(
            rf'data-figure="{testid}".*?data-past-change.*?</div>', card, re.DOTALL
        ).group(0)
        assert "▲ $600<" in past
        assert f"since {label}" in past
        assert "*" not in past  # the only holding had a value then


def test_warnings_card_opens_the_low_point_month(client):
    html = client.get("/s/home").get_data(as_text=True)
    card = _card(html, "projection_warnings")
    assert "1 account dips below its minimum" in card
    assert "Low point" in card
    assert "Chase Checking</span>: under $2,000 min from " in card
    assert "All clear" not in card
    # One link: the card itself, to Projections on the low point's month.
    href = re.match(r'<a href="([^"]+)"', card).group(1)
    assert re.fullmatch(r"/s/home/projections\?months=12&amp;month=\d{4}-\d{2}", href)
    assert card.count("<a ") == 1
    # The link opens that month's breakdown.
    page = client.get(href.replace("&amp;", "&")).get_data(as_text=True)
    assert "data-month-breakdown" in page


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


def test_debt_and_cards_card_follows_cash_flow(client):
    from decimal import Decimal

    from fintrack.networth.repository import add_asset_entry

    engine = client.application.config["engine"]
    with engine.connect() as conn:
        add_asset_entry(
            conn,
            1,
            {
                "kind": "debt",
                "type": "loan",
                "name": "Car",
                "balance": Decimal(12000),
                "interestRate": 0.06,
                "originalPrincipal": 20000,
                "termMonths": 60,
                "originationDate": "2024-01-15",
            },
        )
        add_account(
            conn,
            1,
            {"name": "Visa", "type": "credit_card", "limit": 1000, "balance": -400},
        )
    html = client.get("/s/home").get_data(as_text=True)
    assert html.index('data-overview-card="cash_flow"') < html.index(
        'data-overview-card="debt_cards"'
    )
    card = _card(html, "debt_cards")
    assert "$12,000" in card and "owed on 1 loan" in card
    assert "Interest this month $60" in card
    assert "Next payoff: <span" in card
    assert "40%" in card and "text-amber-700" in card
    assert 'href="/s/home/holdings#holdings-loan"' in card


def test_debt_and_cards_card_without_loans_opens_cards(client):
    card = _card(client.get("/s/home").get_data(as_text=True), "debt_cards")
    assert "No loans" in card
    assert "Card usage" not in card  # no cards with a limit either
    assert 'href="/s/home/holdings#holdings-credit"' in card
