"""Budget sheet history (issue #136): the month pager, edits that apply from
the viewed month, ending/resuming an entry, the history row, and the CLI."""

import re
from datetime import date

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine

from fintrack.budget.repository import (
    add_budget_entry,
    get_budget_entries,
    this_month,
    update_budget_entry,
)
from fintrack.cli import cli
from fintrack.core.db import get_engine, init_db
from fintrack.snapshots.repository import create_snapshot

NOW = this_month()


def _shift(d: date, months: int) -> date:
    i = d.year * 12 + d.month - 1 + months
    return date(i // 12, i % 12 + 1, 1)


PREV, PREV2, NEXT = _shift(NOW, -1), _shift(NOW, -2), _shift(NOW, 1)


def _ym(d: date) -> str:
    return f"{d:%Y-%m}"


@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        for desc, amount, start in (
            ("Rent", 2000, None),
            ("Gym", 40, _ym(NOW) + "-01"),
        ):
            entry = {
                "kind": "expense",
                "description": desc,
                "amount": amount,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            }
            if start:
                entry["startMonth"] = start
            add_budget_entry(conn, sid, entry)
    return engine


@pytest.fixture()
def client(db_engine):
    from web.app import create_app

    app = create_app(db_path=":memory:")
    app.config["TESTING"] = True
    app.config["engine"] = db_engine
    with app.test_client() as c:
        yield c


def _rent(db_engine, month: date):
    with db_engine.connect() as conn:
        return get_budget_entries(conn, 1, as_of=month)[0]


def _rows(body: str) -> list[str]:
    return re.findall(r'id="budget-row-\d+"', body)


def _page(client, month: date | None = None, edit: bool = False) -> str:
    query = []
    if month:
        query.append(f"ym={_ym(month)}")
    if edit:
        query.append("edit=1")
    resp = client.get("/s/home/budget" + ("?" + "&".join(query) if query else ""))
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_sheet_shows_the_viewed_months_plan(client, db_engine):
    with db_engine.connect() as conn:
        update_budget_entry(conn, 1, 0, {"amount": 2100}, effective=NOW)
    now = _page(client)
    assert "($2,100.00)" in now and "Gym" in now
    before = _page(client, PREV)
    assert "($2,000.00)" in before and "($2,100.00)" not in before
    assert "Gym" not in before  # starts this month
    assert f"{PREV:%b %Y}" in before


def test_pager_links_keep_filters_and_edit_mode(client):
    body = _page(client, PREV, edit=True)
    pager = body.split("data-pager>")[1].split("</div>")[0]
    assert f"ym={_ym(PREV2)}" in pager and f"ym={_ym(NOW)}" not in pager
    assert "edit=1" in pager
    # "Now" drops ym (this month is the default).
    assert 'href="/s/home/budget?edit=1"' in pager


def test_past_month_has_no_remaining(client):
    body = _page(client, PREV)
    table = body.split('id="budget-table"')[1]
    assert "—" in table
    assert "The plan as it was in" in body


def test_sheet_urls_carry_the_viewed_month(client):
    body = _page(client, PREV, edit=True)
    assert f"/budget/cell/0?field=amount&amp;ym={_ym(PREV)}" in body
    assert f"/budget/end/0?ym={_ym(PREV)}" in body
    assert f"Edits apply from {PREV:%b %Y}" in body


def test_editing_a_past_month_applies_until_the_next_change(client, db_engine):
    client.post(
        f"/s/home/budget/update/0?ym={_ym(NOW)}",
        data={"field": "amount", "value": "2100"},
    )
    resp = client.post(
        f"/s/home/budget/update/0?ym={_ym(PREV2)}",
        data={"field": "amount", "value": "2050"},
    )
    assert resp.status_code == 200
    assert _rent(db_engine, _shift(PREV2, -1))["amount"] == 2000
    assert _rent(db_engine, PREV2)["amount"] == 2050
    assert _rent(db_engine, PREV)["amount"] == 2050
    assert _rent(db_engine, NOW)["amount"] == 2100
    # The tbody swap is for the viewed month, with its change marked.
    body = resp.get_data(as_text=True)
    assert "($2,050.00)" in body
    assert 'data-budget-mark="changed"' in body


def test_description_edits_apply_to_every_month(client, db_engine):
    client.post(
        f"/s/home/budget/update/0?ym={_ym(PREV)}",
        data={"field": "description", "value": "Lease"},
    )
    assert _rent(db_engine, PREV2)["description"] == "Lease"
    assert _rent(db_engine, NOW)["description"] == "Lease"


def test_add_starts_in_the_viewed_month(client, db_engine):
    client.post(f"/s/home/budget/add?ym={_ym(PREV)}", data={"kind": "expense"})
    with db_engine.connect() as conn:
        added = get_budget_entries(conn, 1)[-1]
    assert added["startMonth"] == PREV.isoformat()


def test_end_and_resume(client, db_engine):
    resp = client.post(f"/s/home/budget/end/0?ym={_ym(PREV)}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "data-budget-resume" in body  # still shown in its last month
    assert 'data-budget-mark="ends"' in body
    assert _rent(db_engine, NOW)["endMonth"] == PREV.isoformat()
    assert "Rent" not in _page(client)
    client.post(f"/s/home/budget/resume/0?ym={_ym(PREV)}")
    assert "endMonth" not in _rent(db_engine, NOW)
    assert "Rent" in _page(client)


def test_history_row_lists_versions(client, db_engine):
    with db_engine.connect() as conn:
        update_budget_entry(conn, 1, 0, {"amount": 2100}, effective=PREV)
    body = client.get(f"/s/home/budget/history/0?ym={_ym(NOW)}").get_data(as_text=True)
    history = body.split('data-history-for="0"')[1].split("</tr>")[0]
    assert re.findall(r'data-version="([^"]+)"', history) == ["base", _ym(PREV)]
    assert 'value="2000.00"' in history and 'value="2100.00"' in history
    closed = client.get(f"/s/home/budget/history/0?ym={_ym(NOW)}&close=1")
    assert "data-history-for" not in closed.get_data(as_text=True)


def test_history_corrects_and_removes_versions(client, db_engine):
    with db_engine.connect() as conn:
        update_budget_entry(conn, 1, 0, {"amount": 2100}, effective=PREV)
    resp = client.post(
        f"/s/home/budget/version/0?ym={_ym(NOW)}",
        data={"from": "base", "amount": "1950"},
    )
    assert resp.status_code == 200
    assert _rent(db_engine, PREV2)["amount"] == 1950
    assert _rent(db_engine, NOW)["amount"] == 2100
    resp = client.post(
        f"/s/home/budget/version/0/delete?ym={_ym(NOW)}", data={"from": _ym(PREV)}
    )
    assert resp.status_code == 200
    assert _rent(db_engine, NOW)["amount"] == 1950
    # The base version can't be removed; junk amounts are rejected.
    bad = client.post(
        f"/s/home/budget/version/0/delete?ym={_ym(NOW)}", data={"from": "base"}
    )
    assert bad.status_code == 422
    bad = client.post(
        f"/s/home/budget/version/0?ym={_ym(NOW)}", data={"from": "base", "amount": "x"}
    )
    assert bad.status_code == 422


def test_span_sets_start_and_end(client, db_engine):
    resp = client.post(
        f"/s/home/budget/span/0?ym={_ym(NOW)}",
        data={"start": _ym(PREV), "end": _ym(NEXT)},
    )
    assert resp.status_code == 200
    rent = _rent(db_engine, NOW)
    assert (rent["startMonth"], rent["endMonth"]) == (
        PREV.isoformat(),
        NEXT.isoformat(),
    )
    bad = client.post(
        f"/s/home/budget/span/0?ym={_ym(NOW)}",
        data={"start": _ym(NEXT), "end": _ym(PREV)},
    )
    assert bad.status_code == 422
    client.post(f"/s/home/budget/span/0?ym={_ym(NOW)}", data={"start": "", "end": ""})
    rent = _rent(db_engine, NOW)
    assert "startMonth" not in rent and "endMonth" not in rent


def test_reorder_moves_only_the_viewed_months_rows(client, db_engine):
    """A band shows only the month's plan; rows outside it keep their slots."""
    with db_engine.connect() as conn:
        add_budget_entry(
            conn,
            1,
            {
                "kind": "expense",
                "description": "Old",
                "amount": 1,
                "recurrence": "monthly",
            },
        )
        # Gym (index 1) isn't planned last month: the band there is Rent, Old.
    resp = client.post(
        f"/s/home/budget/reorder?category=&ym={_ym(PREV)}", data={"order": "1,0"}
    )
    assert resp.status_code == 204
    with db_engine.connect() as conn:
        names = [e["description"] for e in get_budget_entries(conn, 1)]
    assert names == ["Old", "Gym", "Rent"]


def test_budget_and_budget_actual_link_to_each_others_month(client):
    body = _page(client, PREV)
    assert f"/budget/actual?year={PREV.year}&amp;month={PREV.month}" in body
    actual = client.get(
        f"/s/home/budget/actual?year={PREV.year}&month={PREV.month}"
    ).get_data(as_text=True)
    assert f"/budget?ym={_ym(PREV)}&amp;edit=1" in actual


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    engine = get_engine(str(path))
    init_db(engine)
    with engine.connect() as conn:
        sid = create_snapshot(conn, "home")
        add_budget_entry(
            conn,
            sid,
            {
                "kind": "expense",
                "description": "Rent",
                "amount": 2000,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            },
        )
    engine.dispose()
    return path


def _cli(path, *args):
    result = CliRunner().invoke(cli, ["--db", str(path), *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_cli_edit_from_a_month_and_list_any_month(db_path):
    _cli(db_path, "expenses", "edit", "0", "--amount", "2100", "--from", _ym(PREV))
    assert "($2,000.00)" in _cli(db_path, "budget", "--month", _ym(PREV2))
    assert "($2,100.00)" in _cli(db_path, "budget", "--month", _ym(PREV))
    assert "($2,100.00)" in _cli(db_path, "expenses", "--month", _ym(NOW))


def test_cli_history_and_version_fix(db_path):
    _cli(db_path, "expenses", "edit", "0", "--amount", "2100", "--from", _ym(PREV))
    out = _cli(db_path, "expenses", "history", "0")
    assert "base" in out and _ym(PREV) in out
    assert "$2,000.00" in out and "$2,100.00" in out
    _cli(db_path, "expenses", "edit", "0", "--amount", "1950", "--version", "base")
    assert "($1,950.00)" in _cli(db_path, "budget", "--month", _ym(PREV2))
    assert "($2,100.00)" in _cli(db_path, "budget", "--month", _ym(NOW))


def test_cli_end_resume_and_add_from(db_path):
    assert "Ended expense" in _cli(
        db_path, "expenses", "end", "0", "--after", _ym(PREV)
    )
    assert "Rent" not in _cli(db_path, "budget")
    assert "Rent" in _cli(db_path, "budget", "--month", _ym(PREV))
    _cli(db_path, "expenses", "end", "0", "--resume")
    assert "Rent" in _cli(db_path, "budget")
    _cli(
        db_path,
        "expenses",
        "add",
        "--description",
        "Gym",
        "--amount",
        "40",
        "--recurrence",
        "monthly",
        "--from",
        _ym(NEXT),
    )
    assert "Gym" not in _cli(db_path, "budget")
    assert "Gym" in _cli(db_path, "budget", "--month", _ym(NEXT))


def test_cli_rejects_a_bad_month(db_path):
    result = CliRunner().invoke(cli, ["--db", str(db_path), "budget", "--month", "May"])
    assert result.exit_code != 0
    assert "YYYY-MM" in result.output
