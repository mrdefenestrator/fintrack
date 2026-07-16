import json
import logging

import anthropic
from anthropic import Anthropic
from sqlalchemy import Connection

logger = logging.getLogger(__name__)


def _build_prompt(merchant_names: list[str], category_names: list[str]) -> str:
    categories_str = ", ".join(category_names)
    merchants_str = "\n".join(f"- {name}" for name in merchant_names)

    return f"""Classify each merchant name into exactly one spending category.

Categories: {categories_str}

Merchant names:
{merchants_str}

Return a JSON array where each element has "merchant_name" (exactly as given) and "category" (from the list above)."""


def classify_merchants(
    merchant_names: list[str],
    category_names: list[str],
) -> dict[str, str]:
    """Classify merchant names via Claude API. Returns {merchant_name: category}.

    Raises anthropic.APIError or anthropic.APIConnectionError on API failure.
    """
    if not merchant_names:
        return {}

    client = Anthropic()
    prompt = _build_prompt(merchant_names, category_names)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "merchant_name": {"type": "string"},
                            "category": {"type": "string", "enum": category_names},
                        },
                        "required": ["merchant_name", "category"],
                        "additionalProperties": False,
                    },
                },
            }
        },
    )

    classifications = json.loads(response.content[0].text)
    return {item["merchant_name"]: item["category"] for item in classifications}


def _friendly_api_error(e: anthropic.APIError) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "Merchant classification unavailable — ANTHROPIC_API_KEY is not set or invalid."
    if isinstance(e, anthropic.PermissionDeniedError):
        return "Merchant classification unavailable — API key does not have access to this model."
    if isinstance(e, anthropic.RateLimitError):
        return "Merchant classification skipped — rate limit reached, try again later."
    if isinstance(e, anthropic.APIConnectionError):
        return (
            "Merchant classification unavailable — could not connect to Anthropic API."
        )
    msg = getattr(e, "message", str(e))
    return f"Merchant classification failed — {msg}"


def classify_and_cache(
    conn: Connection, merchant_names: list[str]
) -> tuple[int, str | None]:
    """Classify uncached merchants via API and store results.

    Returns (count_classified, warning_message). warning_message is None on success.
    """
    from spending.repository.categories import get_category_names
    from spending.repository.merchants import (
        get_uncached_merchants,
        set_merchant_category,
    )

    try:
        uncached = get_uncached_merchants(conn, merchant_names)
        if not uncached:
            logger.info(
                "Classification skipped — all %d merchants already cached",
                len(merchant_names),
            )
            return 0, None
        logger.info("Sending %d uncached merchants to Claude API", len(uncached))
        category_names = get_category_names(conn)
        classifications = classify_merchants(uncached, category_names)
        for name, category in classifications.items():
            set_merchant_category(conn, name, category, source="api")
        logger.info(
            "Claude classified %d/%d merchants", len(classifications), len(uncached)
        )
        return len(classifications), None
    except anthropic.APIError as e:
        return 0, _friendly_api_error(e)
    except anthropic.APIConnectionError as e:
        return 0, _friendly_api_error(e)
    except Exception:
        logger.exception("Classification failed unexpectedly")
        return 0, "Merchant classification failed unexpectedly."
