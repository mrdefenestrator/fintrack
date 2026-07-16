from unittest.mock import MagicMock, patch

from spending.classifier import classify_merchants, _build_prompt


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

    with patch("spending.classifier.Anthropic", return_value=mock_client):
        result = classify_merchants(
            merchant_names=["WHOLE FOODS", "NETFLIX"],
            category_names=["Groceries", "Subscriptions", "Other"],
        )

    assert result == {"WHOLE FOODS": "Groceries", "NETFLIX": "Subscriptions"}


def test_classify_merchants_empty_list():
    result = classify_merchants(merchant_names=[], category_names=["Groceries"])
    assert result == {}
