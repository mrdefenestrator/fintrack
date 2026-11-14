"""'Group by' on the Merchants and Budget sheets (issue #137): the
flat list by default, or bands on the shared grouped-sheet layer — category
bands in the taxonomy's order and colors (Uncategorized last, slate), plus
Source (Merchants) and Status (Budget) bands."""

import re
import uuid
from datetime import datetime
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine, insert

from fintrack.accounts.repository import add_account
from fintrack.budget.reconcile import link_transaction
from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.db import init_db
from fintrack.core.models import imports, transactions
from fintrack.ledger.repository.categories import add_category
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.snapshots.repository import create_snapshot

TODAY = datetime.now().astimezone().date()


@pytest.fixture()
def db_engine():
    """Three merchants (auto Groceries, manual Housing, auto Uncategorized) with this
    month's transactions, and three budget entries — Rent linked to its
    charge (Matched), Groceries and an uncategorized one not linked."""
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        for name in ("Housing", "Groceries"):  # taxonomy order, not alphabetical
            add_category(conn, name=name)
        sid = create_snapshot(conn, "home")
        acct = add_account(conn, sid, {"name": "Checking", "type": "checking"})
        imp = conn.execute(
            insert(imports).values(
                account_id=acct,
                holding_group="cash",
                filename="s.ofx",
                file_hash=uuid.uuid4().hex,
                status="confirmed",
            )
        ).inserted_primary_key[0]
        txn_ids = {}
        for merchant, amount in (
            ("LANDLORD", "-2000.00"),
            ("SAFEWAY", "-80.00"),
            ("SAFEWAY", "-40.00"),
            ("MYSTERY", "-5.00"),
        ):
            txn_ids[merchant] = conn.execute(
                insert(transactions).values(
                    import_id=imp,
                    account_id=acct,
                    date=TODAY,
                    amount=amount,
                    raw_description=merchant,
                    normalized_merchant=merchant,
                    fingerprint=uuid.uuid4().hex,
                )
            ).inserted_primary_key[0]
        conn.commit()
        set_merchant_category(conn, "LANDLORD", "Housing", source="manual")
        set_merchant_category(conn, "SAFEWAY", "Groceries", source="api")
        set_merchant_category(conn, "MYSTERY", "Uncategorized", source="api")
        for desc, category, amount in (
            ("Rent", "Housing", 2000),
            ("Food", "Groceries", 400),
            ("Misc", None, 20),
        ):
            entry = {
                "kind": "expense",
                "description": desc,
                "amount": amount,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            }
            if category:
                entry["category"] = category
            add_budget_entry(conn, sid, entry)
        rent = get_budget_entries(conn, sid)[0]["_db_id"]
        link_transaction(conn, sid, txn_ids["LANDLORD"], rent)
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _get(client, url: str) -> str:
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _headings(body: str) -> list[str]:
    return re.findall(
        r'<tr class="sheet-group-heading">\s*<td colspan="\d+"[^>]*>([^<]+)</td>', body
    )


def _band(body: str, key: str) -> str:
    return body.split(f'data-group="{key}"')[1].split("</tbody>")[0]


class _Spans(HTMLParser):
    """Each <tr>'s summed colspan inside the page's grouped-sheet table."""

    def __init__(self):
        super().__init__()
        self.inside = False
        self.rows: list[int] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table" and "grouped-sheet" in (a.get("class") or ""):
            self.inside = True
        elif self.inside and tag == "tr":
            self.rows.append(0)
        elif self.inside and tag in ("td", "th") and self.rows:
            self.rows[-1] += int(a.get("colspan") or 1)

    def handle_endtag(self, tag):
        if tag == "table":
            self.inside = False


# --- Merchants ---------------------------------------------------------------


def test_merchants_default_is_flat(client):
    body = _get(client, "/s/home/merchants")
    assert _headings(body) == []
    assert "data-group-by-label>None<" in body


def test_merchants_group_by_category(client):
    body = _get(client, "/s/home/merchants?group=category")
    assert _headings(body) == ["Housing · 1", "Groceries · 1", "Uncategorized · 1"]
    # The Transactions subtotal: SAFEWAY has two charges.
    assert ">2</td>" in _band(body, "merch-cat-groceries").split("</tr>")[0]
    assert 'data-group="merch-cat-housing" data-tone="blue"' in body
    assert 'data-group="merch-cat-uncategorized" data-tone="slate"' in body
    assert "3 merchants" in body  # the pinned footer still counts every row


def test_merchants_group_by_source(client):
    body = _get(client, "/s/home/merchants?group=source")
    heads = _headings(body)
    assert heads == ["Auto · 2", "Manual · 1"]


@pytest.mark.parametrize("group", ["category", "source"])
def test_merchants_rows_span_the_full_grid(client, group):
    parser = _Spans()
    parser.feed(_get(client, f"/s/home/merchants?group={group}&edit=1"))
    assert len(set(parser.rows)) == 1, parser.rows


def test_merchants_recategorizing_regroups_in_place(client):
    body = _get(client, "/s/home/merchants?group=category&edit=1")
    mystery = re.search(
        r'id="merchant-row-(\d+)"', _band(body, "merch-cat-uncategorized")
    )
    resp = client.post(
        f"/s/home/merchants/{mystery.group(1)}/category",
        data={"value": "Groceries"},
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost/s/home/merchants?group=category&edit=1",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["HX-Retarget"] == "#merchants-sheet"
    assert resp.headers["HX-Reswap"] == "innerHTML"
    out = resp.get_data(as_text=True)
    assert _headings(out) == ["Housing · 1", "Groceries · 2"]


def test_merchants_recategorizing_swaps_the_row_when_ungrouped(client):
    body = _get(client, "/s/home/merchants?edit=1")
    row_id = re.search(r'id="merchant-row-(\d+)"', body).group(1)
    resp = client.post(
        f"/s/home/merchants/{row_id}/category",
        data={"value": "Groceries"},
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://localhost/s/home/merchants?edit=1",
        },
    )
    assert "HX-Retarget" not in resp.headers
    assert f'id="merchant-row-{row_id}"' in resp.get_data(as_text=True)


def test_merchants_reset_keeps_the_grouping(client):
    body = _get(client, "/s/home/merchants?group=source&search=SAFE")
    reset = re.search(r'<a href="([^"]+)"[^>]*>Reset</a>', body).group(1)
    assert reset.endswith("?group=source")


# --- Budget: Group by Status (issue #143) ------------------------------------


def test_budget_group_by_status(client):
    """Budget vs Actual's Status grouping moved to the Budget sheet: neutral
    bands in status order, each row keeping its category color, with This
    month / Actual / Delta subtotals (signed: expenses negative)."""
    body = _get(client, "/s/home/budget?group=status")
    assert _headings(body) == ["Matched", "Not linked"]
    matched = _band(body, "status-matched")
    assert "Rent" in matched
    assert 'data-group="status-matched" data-tone="slate"' in body
    heading = matched.split("</tr>")[0]
    # This month, Actual, Delta, (Status), Per month.
    assert re.findall(r">\s*([^<>]*?)\s*</td>", heading)[1:6] == [
        "($2,000.00)",
        "($2,000.00)",
        "$0.00",
        "",
        "($2,000.00)",
    ]
    assert 'data-tone="blue"' in matched  # Rent keeps Housing's color
    # Status bands don't drag-reorder (the stored order is per category).
    edit = _get(client, "/s/home/budget?group=status&edit=1")
    assert "data-reorder-url" not in edit


@pytest.mark.parametrize("group", ["category", "status", "none"])
@pytest.mark.parametrize("edit", ["", "&edit=1"])
def test_budget_rows_span_the_full_grid(client, group, edit):
    parser = _Spans()
    parser.feed(_get(client, f"/s/home/budget?group={group}{edit}"))
    assert len(set(parser.rows)) == 1, parser.rows


def test_budget_pager_and_reset_keep_the_grouping(client):
    body = _get(client, "/s/home/budget?group=status&status=matched")
    pager = body.split("data-pager>")[1].split("data-filter-bar")[0]
    assert "group=status" in pager and "status=matched" in pager
    reset = re.search(r'<a href="([^"]+)"[^>]*>Reset</a>', body).group(1)
    assert "group=status" in reset and "status=matched" not in reset
