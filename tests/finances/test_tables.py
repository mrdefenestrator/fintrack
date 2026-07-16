"""Tests for finances/tables.py — table builders for CLI and web."""

from finances.tables import (
    _account_display_by_id,
    _build_accounts_table,
    _build_budget_table,
    _build_net_worth_table,
    _expected_day_or_date,
    _expected_display,
)


# ---------------------------------------------------------------------------
# _expected_day_or_date
# ---------------------------------------------------------------------------


class TestExpectedDayOrDate:
    def test_monthly_with_day(self):
        assert (
            _expected_day_or_date({"recurrence": "monthly", "dayOfMonth": 15}) == "15th"
        )

    def test_monthly_no_day(self):
        assert _expected_day_or_date({"recurrence": "monthly"}) == "-"

    def test_annual_with_month_and_day(self):
        result = _expected_day_or_date(
            {"recurrence": "annual", "month": 3, "dayOfMonth": 1}
        )
        assert result == "Mar 1st"

    def test_annual_with_day_of_year_fallback(self):
        result = _expected_day_or_date(
            {"recurrence": "annual", "month": 12, "dayOfYear": 25}
        )
        assert result == "Dec 25th"

    def test_annual_missing_fields(self):
        assert _expected_day_or_date({"recurrence": "annual"}) == "-"
        assert _expected_day_or_date({"recurrence": "annual", "month": 3}) == "-"

    def test_quarterly_with_month_and_day(self):
        result = _expected_day_or_date(
            {"recurrence": "quarterly", "month": 1, "dayOfMonth": 10}
        )
        assert result == "Jan 10th"

    def test_quarterly_missing_fields(self):
        assert _expected_day_or_date({"recurrence": "quarterly"}) == "-"

    def test_semiannual_with_month_and_day(self):
        result = _expected_day_or_date(
            {"recurrence": "semiannual", "month": 3, "dayOfMonth": 15}
        )
        # March and September (6 months later)
        assert "Mar" in result
        assert "Sep" in result
        assert "15th" in result

    def test_semiannual_missing_fields(self):
        assert _expected_day_or_date({"recurrence": "semiannual"}) == "-"

    def test_one_time_with_date(self):
        result = _expected_day_or_date({"recurrence": "one_time", "date": "2025-06-15"})
        assert result == "2025-06-15"

    def test_one_time_no_date(self):
        assert _expected_day_or_date({"recurrence": "one_time"}) == "-"

    def test_biweekly(self):
        assert _expected_day_or_date({"recurrence": "biweekly"}) == "-"

    def test_unknown_recurrence(self):
        assert _expected_day_or_date({"recurrence": "daily"}) == "-"


# ---------------------------------------------------------------------------
# _expected_display
# ---------------------------------------------------------------------------


class TestExpectedDisplay:
    def test_continuous_monthly(self):
        entry = {"recurrence": "monthly", "continuous": True, "dayOfMonth": 1}
        assert _expected_display(entry) == "continuous"

    def test_non_continuous_monthly(self):
        entry = {"recurrence": "monthly", "dayOfMonth": 15}
        assert _expected_display(entry) == "15th"

    def test_continuous_non_monthly_falls_through(self):
        # continuous flag on annual should not show "continuous"
        entry = {
            "recurrence": "annual",
            "continuous": True,
            "month": 3,
            "dayOfMonth": 1,
        }
        assert _expected_display(entry) == "Mar 1st"


# ---------------------------------------------------------------------------
# _account_display_by_id
# ---------------------------------------------------------------------------


class TestAccountDisplayById:
    def test_basic(self):
        accounts = [{"id": 1, "name": "Checking"}]
        result = _account_display_by_id(accounts)
        assert result == {1: "Checking"}

    def test_with_institution(self):
        accounts = [{"id": 1, "name": "Checking", "institution": "Chase"}]
        result = _account_display_by_id(accounts)
        assert result == {1: "Chase Checking"}

    def test_with_partial_account_number(self):
        accounts = [{"id": 1, "name": "Checking", "partial_account_number": "1234"}]
        result = _account_display_by_id(accounts)
        assert result == {1: "Checking [1234]"}

    def test_with_all_fields(self):
        accounts = [
            {
                "id": 1,
                "name": "Checking",
                "institution": "Chase",
                "partial_account_number": "1234",
            }
        ]
        result = _account_display_by_id(accounts)
        assert result == {1: "Chase Checking [1234]"}

    def test_skips_no_id(self):
        accounts = [{"name": "NoID"}]
        assert _account_display_by_id(accounts) == {}

    def test_multiple_accounts(self):
        accounts = [
            {"id": 1, "name": "A"},
            {"id": 2, "name": "B", "institution": "Bank"},
        ]
        result = _account_display_by_id(accounts)
        assert result == {1: "A", 2: "Bank B"}


# ---------------------------------------------------------------------------
# _build_accounts_table
# ---------------------------------------------------------------------------


class TestBuildAccountsTable:
    def test_checking_account(self):
        accounts = [{"id": 1, "name": "Main", "type": "checking", "balance": 5000}]
        headers, rows = _build_accounts_table(accounts, n2=5000)
        assert headers[0] == "Institution"
        # First row is the data row
        assert rows[0][2] == "Main"
        assert "$5,000.00" in rows[0][3]
        # Last row is total
        assert rows[-1][0] == "Total"

    def test_credit_card_account(self):
        accounts = [
            {
                "id": 1,
                "name": "Amex",
                "type": "credit_card",
                "limit": 10000,
                "available": 8000,
            }
        ]
        headers, rows = _build_accounts_table(accounts, n2=-2000)
        # Balance should show -(limit - available) = -(10000-8000) = -2000
        assert "($2,000.00)" in rows[0][3]
        assert "$10,000.00" in rows[0][4]  # Limit
        assert "$8,000.00" in rows[0][5]  # Available

    def test_credit_card_with_rewards(self):
        accounts = [
            {
                "id": 1,
                "name": "Amex",
                "type": "credit_card",
                "limit": 10000,
                "available": 8000,
                "rewards_balance": 50,
            }
        ]
        _, rows = _build_accounts_table(accounts, n2=-1950)
        # Balance = (8000 - 10000) + 50 = -1950
        assert "($1,950.00)" in rows[0][3]

    def test_with_show_id(self):
        accounts = [{"id": 42, "name": "X", "type": "checking", "balance": 100}]
        headers, rows = _build_accounts_table(accounts, n2=100, show_id=True)
        assert headers[0] == "ID"
        assert rows[0][0] == "42"

    def test_partial_account_number_in_name(self):
        accounts = [
            {
                "id": 1,
                "name": "Checking",
                "type": "checking",
                "balance": 100,
                "partial_account_number": "9999",
            }
        ]
        _, rows = _build_accounts_table(accounts, n2=100)
        assert "Checking [9999]" in rows[0][2]

    def test_separator_and_total_rows(self):
        accounts = [{"id": 1, "name": "A", "type": "checking", "balance": 100}]
        _, rows = _build_accounts_table(accounts, n2=100)
        # rows: [data_row, separator_row, total_row]
        assert len(rows) == 3
        # Separator row should be all dashes
        assert all(set(cell.strip()) <= {"-"} for cell in rows[1])
        assert rows[2][0] == "Total"

    def test_credit_card_missing_limit_available(self):
        accounts = [{"id": 1, "name": "CC", "type": "credit_card"}]
        _, rows = _build_accounts_table(accounts, n2=0)
        # All numeric fields should be dashes
        assert rows[0][3] == "-"

    def test_statement_fields(self):
        accounts = [
            {
                "id": 1,
                "name": "CC",
                "type": "credit_card",
                "limit": 5000,
                "available": 4500,
                "statement_balance": 450,
                "statement_due_day_of_month": 15,
            }
        ]
        _, rows = _build_accounts_table(accounts, n2=-500)
        assert "$450.00" in rows[0][7]
        assert "15th" in rows[0][8]

    def test_payment_account_ref_on_cc(self):
        accounts = [
            {"id": 1, "name": "Checking", "type": "checking", "balance": 5000},
            {
                "id": 2,
                "name": "Amex",
                "type": "credit_card",
                "limit": 10000,
                "available": 8000,
                "paymentAccountRef": 1,
            },
        ]
        account_display = _account_display_by_id(accounts)
        _, rows = _build_accounts_table(
            accounts, n2=3000, account_display_by_id=account_display
        )
        # CC row (index 1) should show the linked account name in col 9
        assert rows[1][9] == "Checking"

    def test_payment_account_ref_missing_shows_dash(self):
        accounts = [
            {
                "id": 1,
                "name": "Amex",
                "type": "credit_card",
                "limit": 10000,
                "available": 8000,
            }
        ]
        _, rows = _build_accounts_table(accounts, n2=-2000)
        assert rows[0][9] == "-"

    def test_payment_account_ref_non_cc_shows_dash(self):
        accounts = [{"id": 1, "name": "Checking", "type": "checking", "balance": 1000}]
        _, rows = _build_accounts_table(accounts, n2=1000)
        assert rows[0][9] == "-"


# ---------------------------------------------------------------------------
# _build_budget_table
# ---------------------------------------------------------------------------


class TestBuildBudgetTable:
    def test_income_and_expense(self):
        budget = [
            {
                "kind": "income",
                "description": "Salary",
                "amount": 5000,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            },
            {
                "kind": "expense",
                "description": "Rent",
                "amount": 2000,
                "recurrence": "monthly",
                "dayOfMonth": 1,
            },
        ]
        headers, rows = _build_budget_table(budget, 2025, 2, 0)
        assert headers[0] == "Kind"
        # Income row
        assert rows[0][0] == "Income"
        assert rows[0][2] == "Salary"
        # Expense row (amount shown as negative)
        assert rows[1][0] == "Expense"
        assert "($2,000.00)" in rows[1][3]
        # Total row
        assert rows[-1][0] == "Total"

    def test_with_show_index(self):
        budget = [
            {
                "kind": "income",
                "description": "Pay",
                "amount": 100,
                "recurrence": "monthly",
            }
        ]
        headers, rows = _build_budget_table(budget, 2025, 2, 0, show_index=True)
        assert headers[0] == "Index"
        assert rows[0][0] == "0"

    def test_monthly_and_annual_columns(self):
        budget = [
            {
                "kind": "income",
                "description": "Pay",
                "amount": 100,
                "recurrence": "monthly",
            }
        ]
        headers, rows = _build_budget_table(budget, 2025, 2, 0)
        assert "Monthly" in headers
        assert "Annual" in headers
        # Monthly = 100, Annual = 100 * 12 = 1200
        assert "$100.00" in rows[0][7]
        assert "$1,200.00" in rows[0][8]

    def test_auto_account_ref(self):
        account_map = {1: "Chase Checking"}
        budget = [
            {
                "kind": "income",
                "description": "Pay",
                "amount": 100,
                "recurrence": "monthly",
                "autoAccountRef": 1,
            }
        ]
        _, rows = _build_budget_table(
            budget, 2025, 2, 0, account_display_by_id=account_map
        )
        assert rows[0][9] == "Chase Checking"

    def test_auto_account_ref_missing(self):
        budget = [
            {
                "kind": "income",
                "description": "Pay",
                "amount": 100,
                "recurrence": "monthly",
                "autoAccountRef": 99,
            }
        ]
        _, rows = _build_budget_table(budget, 2025, 2, 0)
        assert rows[0][9] == "-"

    def test_empty_budget(self):
        headers, rows = _build_budget_table([], 2025, 2, 0)
        # Should still have separator and total
        assert len(rows) == 2  # separator + total
        assert rows[-1][0] == "Total"


# ---------------------------------------------------------------------------
# _build_net_worth_table
# ---------------------------------------------------------------------------


class TestBuildNetWorthTable:
    def test_assets_and_debts(self):
        entries = [
            {"kind": "asset", "id": 1, "name": "Home", "value": 300000},
            {"kind": "debt", "name": "Mortgage", "balance": 250000, "assetRef": 1},
        ]
        headers, rows = _build_net_worth_table(entries)
        assert headers[0] == "Kind"
        # Asset row
        assert rows[0][0] == "Asset"
        assert rows[0][2] == "Home"
        assert "$300,000.00" in rows[0][3]
        # Debt row
        assert rows[1][0] == "Debt"
        assert "($250,000.00)" in rows[1][5]
        # Debt reference to asset
        assert rows[1][6] == "Home"
        # Total
        assert rows[-1][0] == "Total"

    def test_asset_with_quantity(self):
        entries = [
            {"kind": "asset", "id": 1, "name": "BTC", "value": 50000, "quantity": 0.5}
        ]
        _, rows = _build_net_worth_table(entries)
        # Subtotal = 50000 * 0.5 = 25000
        assert "$25,000.00" in rows[0][5]

    def test_asset_with_institution_and_source(self):
        entries = [
            {
                "kind": "asset",
                "id": 1,
                "name": "401k",
                "value": 100000,
                "institution": "Fidelity",
                "source": "Employer",
            }
        ]
        _, rows = _build_net_worth_table(entries)
        assert rows[0][1] == "Fidelity"
        assert rows[0][6] == "Employer"

    def test_debt_with_interest_rate(self):
        entries = [
            {"kind": "debt", "name": "Loan", "balance": 10000, "interestRate": 0.065}
        ]
        _, rows = _build_net_worth_table(entries)
        assert rows[0][7] == "6.50%"

    def test_debt_no_interest_rate(self):
        entries = [{"kind": "debt", "name": "Loan", "balance": 10000}]
        _, rows = _build_net_worth_table(entries)
        assert rows[0][7] == "-"

    def test_debt_unmatched_asset_ref(self):
        entries = [{"kind": "debt", "name": "Loan", "balance": 5000, "assetRef": 99}]
        _, rows = _build_net_worth_table(entries)
        assert rows[0][6] == "99"

    def test_debt_no_asset_ref(self):
        entries = [{"kind": "debt", "name": "Loan", "balance": 5000}]
        _, rows = _build_net_worth_table(entries)
        assert rows[0][6] == "-"

    def test_with_show_index(self):
        entries = [
            {"kind": "asset", "id": 1, "name": "X", "value": 100},
            {"kind": "debt", "name": "Y", "balance": 50},
        ]
        headers, rows = _build_net_worth_table(entries, show_index=True)
        assert headers[0] == "Index"
        assert rows[0][0] == "0"  # global index 0
        assert rows[1][0] == "1"  # global index 1

    def test_debt_with_quantity(self):
        entries = [
            {
                "kind": "debt",
                "name": "Half Mortgage",
                "balance": 400000,
                "quantity": 0.5,
            }
        ]
        _, rows = _build_net_worth_table(entries)
        # Col 3: per-unit balance
        assert "$400,000.00" in rows[0][3]
        # Col 4: quantity (< 1 displays as percentage)
        assert rows[0][4] == "50%"
        # Col 5: -(balance * quantity) = -(400000 * 0.5) = -200000
        assert "($200,000.00)" in rows[0][5]

    def test_debt_no_quantity_shows_balance_in_value_col(self):
        entries = [{"kind": "debt", "name": "Loan", "balance": 5000}]
        _, rows = _build_net_worth_table(entries)
        # Col 3: balance shown in Value column
        assert "$5,000.00" in rows[0][3]
        # Col 4: no quantity → "-"
        assert rows[0][4] == "-"
        # Col 5: -(balance) = -5000
        assert "($5,000.00)" in rows[0][5]

    def test_total_calculation(self):
        entries = [
            {"kind": "asset", "id": 1, "name": "A", "value": 100000},
            {"kind": "asset", "id": 2, "name": "B", "value": 50000},
            {"kind": "debt", "name": "D", "balance": 30000},
        ]
        _, rows = _build_net_worth_table(entries)
        # Total = 100000 + 50000 - 30000 = 120000
        assert "$120,000.00" in rows[-1][5]

    def test_total_calculation_with_debt_quantity(self):
        entries = [
            {"kind": "asset", "id": 1, "name": "Home", "value": 400000},
            {
                "kind": "debt",
                "name": "Half Mortgage",
                "balance": 400000,
                "quantity": 0.5,
            },
        ]
        _, rows = _build_net_worth_table(entries)
        # Total = 400000 - (400000 * 0.5) = 200000
        assert "$200,000.00" in rows[-1][5]
