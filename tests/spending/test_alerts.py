"""Unusual spending alerts (issue #101): the pure checks."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fintrack.ledger.alerts import (
    AlertConfig,
    charge_alerts,
    load_alert_config,
    running_hot_alerts,
)

TODAY = date(2026, 11, 12)
CFG = AlertConfig()
_ids = iter(range(1, 10_000))


def _txn(day, amount, merchant, category="Shopping", txn_id=None):
    return {
        "id": txn_id or next(_ids),
        "date": day,
        "amount": Decimal(str(amount)),
        "merchant": merchant,
        "category": category,
    }


def _history(merchant, amounts, category="Shopping", start=date(2026, 5, 1)):
    """Earlier monthly charges, well before the alert window."""
    return [
        _txn(start + timedelta(days=30 * i), -a, merchant, category)
        for i, a in enumerate(amounts)
    ]


# --- large charge -----------------------------------------------------------


def test_large_charge_against_the_merchant_median():
    rows = _history("HARDWARE", [40, 50, 60]) + [
        _txn(date(2026, 11, 10), -140, "HARDWARE", txn_id=9001)
    ]
    (alert,) = charge_alerts(rows, TODAY, CFG)
    assert alert["kind"] == "large_charge"
    assert alert["transaction_id"] == 9001
    assert alert["basis"] == "merchant"
    assert alert["baseline"] == 50
    assert alert["message"] == "Large charge: HARDWARE $140 on Nov 10 (usually $50)"


def test_large_charge_needs_both_the_multiple_and_the_dollar_floor():
    # 3x the median but only $20 over: a $10 coffee becoming $30 isn't news.
    small = _history("CAFE", [10, 10, 10]) + [_txn(date(2026, 11, 10), -30, "CAFE")]
    assert charge_alerts(small, TODAY, CFG) == []
    # $60 over but only 1.6x.
    near = _history("GROCER", [100, 100, 100]) + [
        _txn(date(2026, 11, 10), -160, "GROCER")
    ]
    assert charge_alerts(near, TODAY, CFG) == []


def test_large_charge_falls_back_to_the_category_with_little_merchant_history():
    rows = (
        _history("A", [30], "Dining")
        + _history("B", [40], "Dining")
        + _history("C", [50], "Dining")
        + _history("NEWPLACE", [40], "Dining", start=date(2026, 6, 15))
        + [_txn(date(2026, 11, 10), -200, "NEWPLACE", "Dining", txn_id=9002)]
    )
    (alert,) = charge_alerts(rows, TODAY, CFG)
    assert alert["basis"] == "category"
    assert alert["transaction_id"] == 9002
    assert "typical Dining charge $40" in alert["message"]


def test_no_alert_without_enough_history_anywhere():
    rows = _history("A", [30, 30], "Dining") + [
        _txn(date(2026, 11, 10), -500, "A", "Dining")
    ]
    assert charge_alerts(rows, TODAY, CFG) == []


def test_only_earlier_charges_count_as_history():
    # A later big charge doesn't hide an earlier one, and credits aren't charges.
    rows = _history("SHOP", [50, 50, 50]) + [
        _txn(date(2026, 11, 1), 400, "SHOP"),  # refund
        _txn(date(2026, 11, 2), -300, "SHOP", txn_id=9003),
        _txn(date(2026, 11, 3), -300, "SHOP", txn_id=9004),
    ]
    flagged = [a["transaction_id"] for a in charge_alerts(rows, TODAY, CFG)]
    # The second $300 compares against [50, 50, 50, 300] (median 50): flagged too.
    assert flagged == [9004, 9003]


def test_charges_outside_the_window_do_not_alert():
    rows = _history("SHOP", [50, 50, 50]) + [
        _txn(TODAY - timedelta(days=30), -400, "SHOP"),
        _txn(TODAY - timedelta(days=29), -400, "SHOP", txn_id=9005),
    ]
    assert [a["transaction_id"] for a in charge_alerts(rows, TODAY, CFG)] == [9005]


def test_excluded_categories_never_alert():
    rows = _history("BANK", [100, 100, 100], "Transfer") + [
        _txn(date(2026, 11, 10), -5000, "BANK", "Transfer"),
        _txn(date(2026, 11, 10), -5000, "NEWCO", "Income"),
    ]
    assert charge_alerts(rows, TODAY, CFG) == []


# --- new merchant -----------------------------------------------------------


def test_new_merchant_first_charge_over_the_minimum():
    rows = _history("OLD", [20]) + [
        _txn(date(2026, 11, 8), -75, "STRANGER", txn_id=9006),
        _txn(date(2026, 11, 9), -80, "STRANGER"),  # second charge: not new
        _txn(date(2026, 11, 9), -49, "TINY"),  # under $50
    ]
    (alert,) = charge_alerts(rows, TODAY, CFG)
    assert alert["kind"] == "new_merchant"
    assert alert["transaction_id"] == 9006
    assert alert["message"] == "New merchant: STRANGER $75 on Nov 8"


def test_new_merchant_waits_for_enough_history():
    # The snapshot's first row is 40 days back: everything still looks "new".
    rows = [
        _txn(TODAY - timedelta(days=40), -20, "OLD"),
        _txn(date(2026, 11, 8), -75, "STRANGER"),
    ]
    assert charge_alerts(rows, TODAY, CFG) == []


def test_a_merchant_seen_only_in_credits_is_not_new():
    rows = _history("OLD", [20]) + [
        _txn(date(2026, 10, 1), 30, "STORE"),
        _txn(date(2026, 11, 8), -75, "STORE"),
    ]
    assert charge_alerts(rows, TODAY, CFG) == []


# --- running hot ------------------------------------------------------------


def _monthly(category, amount, day=10, months=range(5, 11), year=2026):
    """One charge a month (May-Oct by default), on `day`."""
    return [
        _txn(date(year, m, day), -amount, f"{category} PLACE", category) for m in months
    ]


PAST = _monthly("Dining", 600)


def _variable(amount):
    return {"fixed": 0.0, "variable": float(amount)}


def test_running_hot_against_the_average():
    rows = PAST + [_txn(date(2026, 11, 5), -420, "BISTRO", "Dining")]
    # Past months spent their $600 on the 10th: by the 12th that's all in, so
    # this month's $420 is under the usual - quiet ...
    assert running_hot_alerts(rows, {}, TODAY, CFG) == []
    # ... but spread through the month, $420 by the 12th is on pace for $1,050.
    spread = [
        _txn(date(2026, m, d), -200, "BISTRO", "Dining")
        for m in range(5, 11)
        for d in (5, 15, 25)
    ]
    (alert,) = running_hot_alerts(
        spread + [_txn(date(2026, 11, 5), -420, "BISTRO", "Dining")], {}, TODAY, CFG
    )
    assert alert["kind"] == "running_hot"
    assert alert["category"] == "Dining"
    assert alert["date"] == date(2026, 11, 1)
    assert alert["basis"] == "average"
    assert alert["message"] == (
        "Dining: $420 by the 12th, usually $600 for the whole month"
    )


def test_running_hot_against_a_variable_budget():
    rows = PAST + [_txn(date(2026, 11, 5), -420, "BISTRO", "Dining")]
    # On pace for $1,050: over a $700 budget by 1.5x.
    (alert,) = running_hot_alerts(rows, {"Dining": _variable(700)}, TODAY, CFG)
    assert alert["basis"] == "budget"
    assert alert["message"].endswith("budget $700 for the whole month")
    # A roomy budget stops it.
    assert running_hot_alerts(rows, {"Dining": _variable(900)}, TODAY, CFG) == []


def test_fixed_budget_lines_count_in_full():
    """Rent paid on the 1st is the whole month's rent, not a pace."""
    rent = {"Housing": {"fixed": 1800.0, "variable": 0.0}}
    paid = [_txn(date(2026, 11, 1), -1800, "LANDLORD", "Housing")]
    assert running_hot_alerts(paid, rent, TODAY, CFG) == []
    # Paying 1.5x the rent is still news.
    twice = paid + [_txn(date(2026, 11, 2), -900, "LANDLORD", "Housing")]
    assert len(running_hot_alerts(twice, rent, TODAY, CFG)) == 1


def test_unbudgeted_lump_sums_follow_their_usual_day():
    rows = _monthly("Housing", 1800, day=1) + [
        _txn(date(2026, 11, 1), -1800, "LANDLORD", "Housing")
    ]
    assert running_hot_alerts(rows, {}, TODAY, CFG) == []


def test_a_category_usually_spent_later_counts_the_rest_of_the_month():
    # Usually $300 on the 20th; $400 by the 12th puts the month at $700.
    rows = _monthly("Gifts", 300, day=20) + [
        _txn(date(2026, 11, 3), -400, "SHOP", "Gifts")
    ]
    (alert,) = running_hot_alerts(rows, {}, TODAY, CFG)
    assert alert["category"] == "Gifts"


def test_charges_with_their_own_alert_do_not_make_a_category_hot():
    rows = _monthly("Shopping", 50, day=3) + [
        _txn(date(2026, 11, 9), -300, "HARDWARE", "Shopping", txn_id=9100)
    ]
    assert len(running_hot_alerts(rows, {}, TODAY, CFG)) == 1
    assert running_hot_alerts(rows, {}, TODAY, CFG, skip_ids={9100}) == []


def test_running_hot_is_quiet_early_in_the_month_and_on_pace():
    spread = [
        _txn(date(2026, m, d), -200, "BISTRO", "Dining")
        for m in range(5, 11)
        for d in (5, 15, 25)
    ]
    early = spread + [_txn(date(2026, 11, 2), -500, "BISTRO", "Dining")]
    assert running_hot_alerts(early, {}, date(2026, 11, 6), CFG) == []
    # $250 by the 12th, against a usual $200 by then and $600 a month: $750.
    on_pace = spread + [_txn(date(2026, 11, 5), -250, "BISTRO", "Dining")]
    assert running_hot_alerts(on_pace, {}, TODAY, CFG) == []


def test_running_hot_needs_the_dollar_floor():
    rows = _monthly("Coffee", 40, day=20) + [
        _txn(date(2026, 11, 5), -40, "CAFE", "Coffee")
    ]  # lands at $80: 2x, but only $40 over
    assert running_hot_alerts(rows, {}, TODAY, CFG) == []


def test_running_hot_needs_enough_months_for_an_average():
    rows = _monthly("Dining", 600, months=(9, 10)) + [
        _txn(date(2026, 11, 5), -900, "BISTRO", "Dining")
    ]
    assert running_hot_alerts(rows, {}, TODAY, CFG) == []
    # With a budget, history doesn't matter.
    assert len(running_hot_alerts(rows, {"Dining": _variable(600)}, TODAY, CFG)) == 1


def test_running_hot_skips_excluded_and_unseen_categories():
    rows = PAST + [
        _txn(date(2026, 11, 5), -5000, "BANK", "Transfer"),
        _txn(date(2026, 11, 5), -900, "AIRLINE", "Travel"),
    ]
    # Travel never appeared in the past six months: its average is 0.
    assert running_hot_alerts(rows, {}, TODAY, CFG) == []


# --- config -----------------------------------------------------------------


def test_repo_config_matches_the_defaults():
    assert load_alert_config() == AlertConfig()


def test_config_overrides_and_rejects_typos(tmp_path):
    path = tmp_path / "alerts.yaml"
    path.write_text("window_days: 14\nexcluded_categories: [Transfer]\n")
    cfg = load_alert_config(path)
    assert cfg.window_days == 14
    assert cfg.excluded_categories == ("Transfer",)
    assert load_alert_config(tmp_path / "missing.yaml") == AlertConfig()
    path.write_text("window_dayz: 14\n")
    with pytest.raises(ValueError, match="window_dayz"):
        load_alert_config(path)
