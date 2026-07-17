"""Tests for the import page's quick-create-account endpoint
(web/routes/imports.py:create_account), covering the canonical account-type
list (issue 3): every type in fintrack.core.types.ACCOUNT_TYPE_OPTIONS must
be accepted, and the quick-create select must offer all of them.
"""

import pytest

from fintrack.core.types import ACCOUNT_TYPE_OPTIONS, ACCOUNT_TYPE_VALUES
from web.routes.imports import VALID_ACCOUNT_TYPES


def test_valid_account_types_matches_canonical_list():
    """imports.py's validation set is sourced from the single canonical list."""
    assert VALID_ACCOUNT_TYPES == set(ACCOUNT_TYPE_VALUES)


@pytest.mark.parametrize("account_type", ACCOUNT_TYPE_VALUES)
def test_create_account_accepts_every_canonical_type(client, conn, account_type):
    response = client.post(
        "/s/ledger/import/accounts",
        data={
            "acct_name": f"Test {account_type}",
            "acct_institution": "Test Bank",
            "acct_type": account_type,
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert f"Test {account_type}" in html
    assert "already exists" not in html


def test_import_page_quick_create_select_offers_all_canonical_types(client):
    response = client.get("/s/ledger/import")
    html = response.data.decode()
    for value, label in ACCOUNT_TYPE_OPTIONS:
        assert f'value="{value}"' in html
        assert label in html
