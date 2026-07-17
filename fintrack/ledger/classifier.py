import json
import logging

import anthropic
from anthropic import Anthropic
from sqlalchemy import Connection

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
DEFAULT_CHUNK_SIZE = 40
MAX_TOKENS = 4096


def _build_prompt(merchant_names: list[str], category_names: list[str]) -> str:
    categories_str = ", ".join(category_names)
    merchants_str = "\n".join(f"- {name}" for name in merchant_names)

    return f"""Classify each merchant name into exactly one spending category.

Categories: {categories_str}

Merchant names:
{merchants_str}

Return a JSON array where each element has "merchant_name" (exactly as given) and "category" (from the list above)."""


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _output_schema(category_names: list[str]) -> dict:
    return {
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
    }


def _classify_chunk(
    client: Anthropic,
    merchant_names: list[str],
    category_names: list[str],
) -> dict[str, str]:
    """Classify a single batch of merchants. Returns {merchant_name: category}.

    A response truncated at max_tokens or that isn't valid JSON is logged and
    treated as an empty result for this batch rather than raised — one bad
    batch should never sink the whole import. Anthropic API failures (auth,
    rate limit, connection, etc.) propagate to the caller.
    """
    prompt = _build_prompt(merchant_names, category_names)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config=_output_schema(category_names),
    )

    if response.stop_reason == "max_tokens":
        logger.warning(
            "Classification response truncated at max_tokens for a batch of "
            "%d merchants — skipping batch",
            len(merchant_names),
        )
        return {}

    try:
        classifications = json.loads(response.content[0].text)
    except json.JSONDecodeError:
        logger.warning(
            "Classification response was not valid JSON for a batch of %d "
            "merchants — skipping batch",
            len(merchant_names),
        )
        return {}

    requested = set(merchant_names)
    result: dict[str, str] = {}
    for item in classifications:
        name = item.get("merchant_name")
        if name not in requested:
            logger.warning("Ignoring classification for unrequested merchant %r", name)
            continue
        result[name] = item["category"]
    return result


def classify_merchants(
    merchant_names: list[str],
    category_names: list[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> dict[str, str]:
    """Classify merchant names via Claude API. Returns {merchant_name: category}.

    Merchants are sent to the API in batches of `chunk_size` so no single
    response can grow large enough to be truncated. A batch that comes back
    truncated or malformed is skipped (logged) rather than failing the whole
    call — other batches still succeed.

    Raises anthropic.APIError or anthropic.APIConnectionError on API failure.
    """
    if not merchant_names:
        return {}

    client = Anthropic()
    results: dict[str, str] = {}
    for batch in _chunked(merchant_names, chunk_size):
        results.update(_classify_chunk(client, batch, category_names))
    return results


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
    conn: Connection,
    merchant_names: list[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[int, str | None]:
    """Classify uncached merchants via API and store results.

    Merchants are classified one batch at a time, and each batch's
    successful classifications are written to the merchant cache
    immediately — so a later batch failing (truncated response, malformed
    JSON, or an API error) never loses the results already saved from
    earlier batches.

    Returns (count_classified, warning_message). warning_message is None on
    full success.
    """
    from fintrack.ledger.repository.categories import get_category_names
    from fintrack.ledger.repository.merchants import (
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
        client = Anthropic()

        classified_count = 0
        failed_count = 0
        last_api_error: anthropic.APIError | anthropic.APIConnectionError | None = None

        for batch in _chunked(uncached, chunk_size):
            try:
                classifications = _classify_chunk(client, batch, category_names)
            except (anthropic.APIError, anthropic.APIConnectionError) as e:
                logger.warning(
                    "Classification API call failed for a batch of %d merchants: %s",
                    len(batch),
                    e,
                )
                last_api_error = e
                failed_count += len(batch)
                continue

            for name, category in classifications.items():
                set_merchant_category(conn, name, category, source="api")
            classified_count += len(classifications)
            failed_count += len(batch) - len(classifications)

        total = len(uncached)

        if failed_count == 0:
            logger.info("Claude classified %d/%d merchants", classified_count, total)
            return classified_count, None

        if classified_count == 0 and last_api_error is not None:
            return 0, _friendly_api_error(last_api_error)

        logger.info(
            "Claude classified %d/%d merchants (%d failed)",
            classified_count,
            total,
            failed_count,
        )
        return classified_count, (
            f"Classified {classified_count} of {total} merchants — "
            f"{failed_count} could not be classified."
        )
    except anthropic.APIError as e:
        return 0, _friendly_api_error(e)
    except anthropic.APIConnectionError as e:
        return 0, _friendly_api_error(e)
    except Exception:
        logger.exception("Classification failed unexpectedly")
        return 0, "Merchant classification failed unexpectedly."
