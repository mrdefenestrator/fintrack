from unittest.mock import MagicMock, patch

from fintrack.ledger.classifier import (
    classify_and_cache,
    classify_merchants,
    _build_prompt,
)
from fintrack.ledger.repository.categories import add_category
from fintrack.ledger.repository.merchants import list_merchants


def _mock_response(text: str, stop_reason: str = "end_turn") -> MagicMock:
    response = MagicMock()
    response.stop_reason = stop_reason
    response.content = [MagicMock(text=text)]
    return response


def test_build_prompt():
    prompt = _build_prompt(
        merchant_names=["WHOLE FOODS", "NETFLIX"],
        category_names=["Groceries", "Subscriptions", "Other"],
    )
    assert "WHOLE FOODS" in prompt
    assert "NETFLIX" in prompt
    assert "Groceries" in prompt
    assert "JSON" in prompt


def test_classify_merchants_returns_mapping():
    mock_response = MagicMock()
    mock_response.content = [
        MagicMock(
            text='[{"merchant_name": "WHOLE FOODS", "category": "Groceries"}, {"merchant_name": "NETFLIX", "category": "Subscriptions"}]'
        )
    ]

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["WHOLE FOODS", "NETFLIX"],
            category_names=["Groceries", "Subscriptions", "Other"],
        )

    assert result == {"WHOLE FOODS": "Groceries", "NETFLIX": "Subscriptions"}


def test_classify_merchants_empty_list():
    result = classify_merchants(merchant_names=[], category_names=["Groceries"])
    assert result == {}


def test_classify_merchants_ignores_hallucinated_merchant():
    mock_response = _mock_response(
        '[{"merchant_name": "WHOLE FOODS", "category": "Groceries"}, '
        '{"merchant_name": "MADE UP STORE", "category": "Other"}]'
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["WHOLE FOODS"],
            category_names=["Groceries", "Other"],
        )

    assert result == {"WHOLE FOODS": "Groceries"}


def test_classify_merchants_multi_chunk_success():
    # 5 merchants with chunk_size=2 -> 3 API calls (2, 2, 1)
    merchants = ["A", "B", "C", "D", "E"]
    responses = [
        _mock_response(
            '[{"merchant_name": "A", "category": "Other"}, {"merchant_name": "B", "category": "Other"}]'
        ),
        _mock_response(
            '[{"merchant_name": "C", "category": "Other"}, {"merchant_name": "D", "category": "Other"}]'
        ),
        _mock_response('[{"merchant_name": "E", "category": "Other"}]'),
    ]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = responses

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=merchants,
            category_names=["Other"],
            chunk_size=2,
        )

    assert result == {name: "Other" for name in merchants}
    assert mock_client.messages.create.call_count == 3


def test_classify_merchants_skips_max_tokens_chunk():
    # First chunk truncated at max_tokens, second chunk succeeds.
    responses = [
        _mock_response('[{"merchant_name": "A", "cat', stop_reason="max_tokens"),
        _mock_response('[{"merchant_name": "B", "category": "Other"}]'),
    ]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = responses

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["A", "B"],
            category_names=["Other"],
            chunk_size=1,
        )

    assert result == {"B": "Other"}


def test_classify_merchants_skips_malformed_json_chunk():
    responses = [
        _mock_response("not valid json"),
        _mock_response('[{"merchant_name": "B", "category": "Other"}]'),
    ]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = responses

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["A", "B"],
            category_names=["Other"],
            chunk_size=1,
        )

    assert result == {"B": "Other"}


def _seed_category(conn, name="Other"):
    add_category(conn, name=name, sort_order=0)


def test_classify_and_cache_all_success(conn):
    _seed_category(conn)
    response = _mock_response(
        '[{"merchant_name": "A", "category": "Other"}, {"merchant_name": "B", "category": "Other"}]'
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = response

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        count, warning = classify_and_cache(conn, ["A", "B"])

    assert count == 2
    assert warning is None
    cached = {m["merchant_name"]: m["category"] for m in list_merchants(conn)}
    assert cached == {"A": "Other", "B": "Other"}


def test_classify_and_cache_partial_failure_max_tokens_still_caches_success(conn):
    _seed_category(conn)
    responses = [
        _mock_response('[{"merchant_name": "A", "category": "Other"}]'),
        _mock_response('[{"merchant_name": "B", "cat', stop_reason="max_tokens"),
    ]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = responses

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        count, warning = classify_and_cache(conn, ["A", "B"], chunk_size=1)

    assert count == 1
    assert warning == "Classified 1 of 2 merchants — 1 could not be classified."
    cached = {m["merchant_name"]: m["category"] for m in list_merchants(conn)}
    assert cached == {"A": "Other"}


def test_classify_and_cache_malformed_json_does_not_lose_other_batches(conn):
    _seed_category(conn)
    responses = [
        _mock_response("this is not json"),
        _mock_response('[{"merchant_name": "B", "category": "Other"}]'),
    ]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = responses

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        count, warning = classify_and_cache(conn, ["A", "B"], chunk_size=1)

    assert count == 1
    assert warning == "Classified 1 of 2 merchants — 1 could not be classified."
    cached = {m["merchant_name"]: m["category"] for m in list_merchants(conn)}
    assert cached == {"B": "Other"}


def test_classify_and_cache_full_api_failure_returns_friendly_message(conn):
    import anthropic
    import httpx

    _seed_category(conn)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=401, request=request)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = anthropic.AuthenticationError(
        message="invalid key",
        response=response,
        body=None,
    )

    with patch("fintrack.ledger.classifier.Anthropic", return_value=mock_client):
        count, warning = classify_and_cache(conn, ["A"])

    assert count == 0
    assert warning == (
        "Merchant classification unavailable — ANTHROPIC_API_KEY is not set or invalid."
    )
    assert list_merchants(conn) == []


def test_classify_and_cache_no_uncached_merchants(conn):
    _seed_category(conn)
    from fintrack.ledger.repository.merchants import set_merchant_category

    set_merchant_category(conn, "A", "Other", source="manual")

    count, warning = classify_and_cache(conn, ["A"])

    assert count == 0
    assert warning is None
