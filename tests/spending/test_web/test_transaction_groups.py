"""Transactions "Group by" (issue #137): the flat list by default, or one band
per category / account — heading with the row count and Amount subtotal, its
own sortable header — on the shared grouped-sheet layer."""

import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import create_engine

from fintrack.budget.repository import add_budget_entry, get_budget_entries
from fintrack.core.db import init_db
from fintrack.ledger.repository.accounts import add_account
from fintrack.ledger.repository.categories import add_category
from fintrack.ledger.repository.imports import (
    confirm_import,
    create_import,
    insert_transactions,
)
from fintrack.ledger.repository.merchants import set_merchant_category
from fintrack.ledger.repository.transactions import get_transactions
from fintrack.snapshots.repository import create_snapshot

_URL = "/s/home/transactions?year=2024&month=1"


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        # Taxonomy order deliberately not alphabetical.
        add_category(conn, name="Groceries")
        add_category(conn, name="Dining")
        sid = create_snapshot(conn, "home")
        accts = [
            add_account(
                conn,
                name=name,
                institution=inst,
                account_type="credit_card",
                snapshot_id=sid,
            )
            for inst, name in [("Chase", "Sapphire"), ("Amex", "Gold")]
        ]
        rows = [
            # (account, merchant, amount, day)
            (0, "WHOLE FOODS", "-40.00", 3),
            (1, "CHIPOTLE", "-12.00", 4),
            (0, "MYSTERY SHOP", "-5.00", 5),
            (1, "WHOLE FOODS", "-60.00", 6),
        ]
        for n, acct in enumerate(accts):
            imp = create_import(
                conn, account_id=acct, filename=f"{n}.ofx", file_hash=f"h{n}"
            )
            confirm_import(conn, imp)
            insert_transactions(
                conn,
                import_id=imp,
                account_id=acct,
                transactions_data=[
                    {
                        "date": date(2024, 1, day),
                        "amount": Decimal(amount),
                        "raw_description": merchant,
                        "normalized_merchant": merchant,
                        "fingerprint": f"fp{i}",
                    }
                    for i, (a, merchant, amount, day) in enumerate(rows)
                    if a == n
                ],
            )
        set_merchant_category(conn, "WHOLE FOODS", "Groceries", source="api")
        set_merchant_category(conn, "CHIPOTLE", "Dining", source="api")
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _get(client, qs="", **headers) -> str:
    return client.get(_URL + qs, headers=headers).get_data(as_text=True)


def _headings(body: str) -> list[str]:
    return re.findall(
        r'<tr class="sheet-group-heading">\s*<td colspan="\d+"[^>]*>([^<]+)</td>', body
    )


def _band(body: str, key: str) -> str:
    return body.split(f'data-group="{key}"')[1].split("</tbody>")[0]


def _txn_id(db_engine, merchant: str, day: int) -> int:
    with db_engine.connect() as conn:
        txns = get_transactions(conn, year=2024, month=1, snapshot_id=1)
    return next(
        t["id"]
        for t in txns
        if t["raw_description"] == merchant and t["date"].day == day
    )


def test_default_is_the_flat_list(client):
    body = _get(client)
    assert 'id="txn-tbody"' in body
    assert _headings(body) == []
    assert "data-group-by-label>None<" in body


def test_group_by_category_bands_in_taxonomy_order(client):
    body = _get(client, "&group=category")
    assert _headings(body) == [
        "Groceries · 2",
        "Dining · 1",
        "Uncategorized · 1",
    ]
    groceries = _band(body, "txn-cat-groceries")
    assert groceries.count('id="txn-row-') == 2
    assert "-$100.00" in groceries  # the band's Amount subtotal
    # Category colors match Budget's (taxonomy order); Uncategorized is slate.
    assert re.search(r'data-group="txn-cat-groceries" data-tone="blue"', body)
    assert re.search(r'data-group="txn-cat-dining" data-tone="amber"', body)
    assert re.search(r'data-group="txn-cat-uncategorized" data-tone="slate"', body)
    # Each band has its own sortable header; the pinned footer totals all rows.
    assert body.count('class="sheet-group-header') == 3
    assert "4 transactions" in body
    assert "-$117.00" in body


def test_group_by_account_bands_follow_account_order(client):
    body = _get(client, "&group=account")
    heads = _headings(body)
    assert sorted(heads) == ["Amex Gold · 2", "Chase Sapphire · 2"]
    tones = re.findall(r'data-group="txn-acct-\d+" data-tone="(\w+)"', body)
    assert tones == ["slate", "slate"]


def test_unknown_group_falls_back_to_default(client):
    assert 'id="txn-tbody"' in _get(client, "&group=bogus")


def test_time_nav_and_reset_keep_the_grouping(client):
    body = _get(client, "&group=account")
    pager = body.split("data-pager-row")[1].split("data-filter-bar")[0]
    assert "group=account" in pager
    reset = re.search(r'<a href="([^"]+)"[^>]*>Reset</a>', body)
    assert reset is None or "group=account" in reset.group(1)


class _Spans(HTMLParser):
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


@pytest.mark.parametrize("group", ["category", "account"])
@pytest.mark.parametrize("edit", ["", "&edit=1"])
def test_every_row_spans_the_full_grid(client, group, edit):
    parser = _Spans()
    parser.feed(_get(client, f"&group={group}{edit}"))
    assert len(set(parser.rows)) == 1, parser.rows


def _regrouped(resp) -> str:
    """The in-place table refresh an edit answers with while grouped by
    category: the whole table, retargeted into the scroll container."""
    assert resp.status_code == 200
    assert resp.headers["HX-Retarget"] == "#txn-sheet"
    assert resp.headers["HX-Reswap"] == "innerHTML"
    assert "HX-Redirect" not in resp.headers
    return resp.get_data(as_text=True)


def test_recategorizing_regroups_the_table_in_place(client, db_engine):
    """No full reload (it would jump back to the top): the table re-renders
    into its scroll container for the page's own filters and window."""
    txn = _txn_id(db_engine, "MYSTERY SHOP", 5)
    page = f"http://localhost{_URL}&group=category&edit=1"
    body = _regrouped(
        client.post(
            f"/s/home/transactions/{txn}/update",
            data={"field": "category", "value": "Dining"},
            headers={"HX-Request": "true", "HX-Current-URL": page},
        )
    )
    assert "<html" not in body
    assert _headings(body) == ["Groceries · 2", "Dining · 2"]
    assert f'id="txn-row-{txn}"' in _band(body, "txn-cat-dining")
    assert "data-group-by" not in body  # just the table, not the filter bar


def test_regroup_keeps_the_page_filters(client, db_engine):
    txn = _txn_id(db_engine, "MYSTERY SHOP", 5)
    page = f"http://localhost{_URL}&group=category&q=MYSTERY"
    body = _regrouped(
        client.post(
            f"/s/home/transactions/{txn}/update",
            data={"field": "category", "value": "Dining"},
            headers={"HX-Request": "true", "HX-Current-URL": page},
        )
    )
    assert _headings(body) == ["Dining · 1"]


def test_budget_link_regroups_when_grouped_by_category(client, db_engine):
    """A budget link pins the transaction's category to the entry's, so it
    moves bands too."""
    with db_engine.connect() as conn:
        add_budget_entry(
            conn,
            1,
            {
                "kind": "expense",
                "description": "Eating out",
                "amount": 50,
                "recurrence": "monthly",
                "category": "Dining",
            },
        )
        ref = get_budget_entries(conn, 1)[0]["_db_id"]
    txn = _txn_id(db_engine, "MYSTERY SHOP", 5)
    body = _regrouped(
        client.post(
            f"/s/home/transactions/{txn}/link",
            data={"budget_entry_ref": str(ref)},
            headers={
                "HX-Request": "true",
                "HX-Current-URL": f"http://localhost{_URL}&group=category&edit=1",
            },
        )
    )
    assert f'id="txn-row-{txn}"' in _band(body, "txn-cat-dining")
    assert "Uncategorized" not in _headings(body)


def test_group_by_month_bands_newest_first(client, db_engine):
    # A February row joins January's four; the Year window shows both months.
    with db_engine.connect() as conn:
        imp = create_import(conn, account_id=1, filename="feb.ofx", file_hash="hf")
        confirm_import(conn, imp)
        insert_transactions(
            conn,
            import_id=imp,
            account_id=1,
            transactions_data=[
                {
                    "date": date(2024, 2, 9),
                    "amount": Decimal("-8.00"),
                    "raw_description": "CHIPOTLE",
                    "normalized_merchant": "CHIPOTLE",
                    "fingerprint": "fp-feb",
                }
            ],
        )
    body = client.get(
        "/s/home/transactions?year=2024&month=1&all_months=year&group=month"
    ).get_data(as_text=True)
    assert _headings(body) == ["Feb 2024 · 1", "Jan 2024 · 4"]
    assert "-$8.00" in _band(body, "txn-month-2024-02")
    assert "-$117.00" in _band(body, "txn-month-2024-01")


def test_recategorizing_swaps_the_row_when_not_grouped_by_category(client, db_engine):
    txn = _txn_id(db_engine, "MYSTERY SHOP", 5)
    resp = client.post(
        f"/s/home/transactions/{txn}/update",
        data={"field": "category", "value": "Dining"},
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://localhost{_URL}&group=account",
        },
    )
    assert resp.status_code == 200
    assert f'id="txn-row-{txn}"' in resp.get_data(as_text=True)
