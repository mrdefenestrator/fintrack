from spending.repository.accounts import add_account


def test_create_account_success(client):
    response = client.post(
        "/accounts",
        data={
            "acct_name": "Chase Checking",
            "acct_institution": "Chase",
            "acct_type": "checking",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "Chase Checking" in html
    assert "selected" in html


def test_create_account_duplicate_name_shows_error(client, conn):
    add_account(
        conn, name="Chase Checking", institution="Chase", account_type="checking"
    )
    response = client.post(
        "/accounts",
        data={
            "acct_name": "Chase Checking",
            "acct_institution": "Chase",
            "acct_type": "checking",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "already exists" in html


def test_create_account_missing_name_shows_error(client):
    response = client.post(
        "/accounts",
        data={
            "acct_name": "",
            "acct_institution": "Chase",
            "acct_type": "checking",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "required" in html


def test_create_account_missing_institution_shows_error(client):
    response = client.post(
        "/accounts",
        data={
            "acct_name": "Chase Checking",
            "acct_institution": "",
            "acct_type": "checking",
        },
    )
    assert response.status_code == 200
    html = response.data.decode()
    assert "required" in html
