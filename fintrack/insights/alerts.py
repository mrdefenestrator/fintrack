"""Spending alerts for a snapshot (issue #101): gathers the data the pure
checks in fintrack.ledger.alerts need and drops dismissed alerts.

Shared by the Overview "Needs attention" card, the Transactions sheet's
markers and Flagged filter, the import confirmation, `fintrack alerts`, and
`status`. Local only: no transaction data leaves the machine.
"""

from datetime import date

from sqlalchemy.engine import Connection

from fintrack.budget.recurrence import budget_entry_in_month
from fintrack.budget.repository import get_budget_entries
from fintrack.ledger.alerts import (
    Alert,
    AlertConfig,
    CategoryBudget,
    charge_alerts,
    load_alert_config,
    running_hot_alerts,
)
from fintrack.ledger.repository.alerts import get_alert_history, get_dismissals


def is_dismissed(
    alert: Alert, txn_ids: set[int], categories: set[tuple[str, date]]
) -> bool:
    if alert["transaction_id"] is not None:
        return alert["transaction_id"] in txn_ids
    return (alert["category"], alert["date"]) in categories


def get_alerts(
    conn: Connection,
    snapshot_id: int,
    today: date,
    *,
    config: AlertConfig | None = None,
    include_dismissed: bool = False,
) -> list[Alert]:
    """The snapshot's open alerts: categories running hot (biggest overshoot
    first), then flagged charges (newest first)."""
    _, alerts = _gather(conn, snapshot_id, today, config or load_alert_config())
    return alerts if include_dismissed else _open(conn, snapshot_id, alerts)


def alerts_by_transaction(
    conn: Connection,
    snapshot_id: int,
    today: date,
    *,
    config: AlertConfig | None = None,
) -> dict[int, Alert]:
    """The rows behind each open alert, keyed by transaction id: the Flagged
    filter and the row markers. A charge alert flags its own row; a running-hot
    alert flags that category's rows this month, so the Overview card's one
    link (Flagged) always shows what it counts. A row's own alert wins."""
    history, alerts = _gather(conn, snapshot_id, today, config or load_alert_config())
    alerts = _open(conn, snapshot_id, alerts)
    hot = {a["category"]: a for a in alerts if a["kind"] == "running_hot"}
    flagged: dict[int, Alert] = {}
    if hot:
        month_start = today.replace(day=1)
        for row in history:
            if month_start <= row["date"] <= today and row["category"] in hot:
                flagged[row["id"]] = hot[row["category"]]
    for a in alerts:
        if a["transaction_id"] is not None:
            flagged[a["transaction_id"]] = a
    return flagged


def _gather(
    conn: Connection, snapshot_id: int, today: date, cfg: AlertConfig
) -> tuple[list[dict], list[Alert]]:
    """(history rows, every alert dismissed or not)."""
    history = get_alert_history(conn, snapshot_id, today)
    charges = charge_alerts(history, today, cfg)
    hot = running_hot_alerts(
        history,
        _budget_by_category(conn, snapshot_id, today),
        today,
        cfg,
        # One big charge is one alert: it doesn't also make its category hot.
        skip_ids={a["transaction_id"] for a in charges},
    )
    return history, hot + charges


def get_import_alerts(
    conn: Connection,
    snapshot_id: int,
    import_id: int,
    today: date,
    *,
    config: AlertConfig | None = None,
) -> list[Alert]:
    """Open alerts on one import's rows: the same alerts the Flagged view
    shows, limited to that import (its confirmation summary)."""
    cfg = config or load_alert_config()
    history = get_alert_history(conn, snapshot_id, today)
    ids = {r["id"] for r in history if r["import_id"] == import_id}
    if not ids:
        return []
    alerts = [
        a for a in charge_alerts(history, today, cfg) if a["transaction_id"] in ids
    ]
    return _open(conn, snapshot_id, alerts)


def _open(conn: Connection, snapshot_id: int, alerts: list[Alert]) -> list[Alert]:
    txn_ids, categories = get_dismissals(conn, snapshot_id)
    return [a for a in alerts if not is_dismissed(a, txn_ids, categories)]


def _budget_by_category(
    conn: Connection, snapshot_id: int, today: date
) -> dict[str, CategoryBudget]:
    """Each category's planned spending for today's month, split like Budget
    vs Actual: continuous monthly lines accrue, every other line is fixed."""
    budget: dict[str, CategoryBudget] = {}
    for entry in get_budget_entries(conn, snapshot_id):
        category = entry.get("category")
        if entry.get("kind") != "expense" or not category:
            continue
        amount = float(budget_entry_in_month(entry, today.year, today.month))
        plan = budget.setdefault(category, {"fixed": 0.0, "variable": 0.0})
        continuous = entry.get("recurrence") == "monthly" and entry.get("continuous")
        plan["variable" if continuous else "fixed"] += amount
    return budget
