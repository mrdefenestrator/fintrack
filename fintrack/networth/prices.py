"""External price lookups and local cache for non-USD asset units.

Fetches current prices from free, no-key APIs:
- CoinGecko (keyless public API) for crypto (BTC, ETH, …)
- Yahoo Finance for stocks (AAPL, GOOGL, …)

Prices are cached in the ``price_cache`` table and refreshed when stale
(>24 h by default).  The public entry point is ``get_rates``, which
returns a ``{unit: Decimal}`` mapping suitable for passing straight into
the calculations engine.

Privacy: only ticker symbols are ever sent to external APIs — no amounts,
dates, account numbers, or personal data.
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, text

from fintrack.core.models import price_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

STALENESS_THRESHOLD = timedelta(hours=24)
FETCH_TIMEOUT = 5  # seconds

# Known crypto symbols → CoinGecko coin IDs.
# CoinGecko uses slug-style ids (e.g. "bitcoin"), not ticker symbols.
CRYPTO_IDS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "LTC": "litecoin",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "BNB": "binancecoin",
    "SHIB": "shiba-inu",
    "NEAR": "near-protocol",
    "ARB": "arbitrum",
    "OP": "optimism",
    "FIL": "filecoin",
    "APT": "aptos",
}

# Reverse mapping: CoinGecko id → symbol (for parsing responses).
_ID_TO_SYMBOL: dict[str, str] = {v: k for k, v in CRYPTO_IDS.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_rates(conn, units: set[str]) -> dict[str, Decimal]:
    """Return current USD prices for the requested *units*.

    Checks the local ``price_cache`` first; stale entries (older than
    ``STALENESS_THRESHOLD``) are refreshed from external APIs.  API
    failures are logged and silently swallowed — cached values (even
    stale ones) are returned, and units with no cached price at all are
    simply omitted from the result.

    The caller is responsible for falling back to the per-row ``value``
    field when a unit is missing from the returned dict.
    """
    if not units:
        return {}

    cached = _read_cache(conn, units)
    now = datetime.now(timezone.utc)

    fresh: dict[str, Decimal] = {}
    stale_units: set[str] = set()

    for unit in units:
        if unit in cached:
            price, fetched_at = cached[unit]
            fresh[unit] = price
            if now - fetched_at > STALENESS_THRESHOLD:
                stale_units.add(unit)
        else:
            stale_units.add(unit)

    if stale_units:
        fetched = _fetch_prices(stale_units)
        if fetched:
            _write_cache(conn, fetched)
            fresh.update(fetched)

    return fresh


def get_price_meta(conn, units: set[str]) -> dict[str, datetime]:
    """Return ``{unit: fetched_at}`` (tz-aware) for the cached *units*.

    Companion to :func:`get_rates` for callers that also want to show how
    fresh each cached price is (the web Holdings sheet). Units with no cache
    entry are simply omitted. Never triggers an external fetch.
    """
    if not units:
        return {}
    return {
        unit: fetched_at for unit, (_, fetched_at) in _read_cache(conn, units).items()
    }


def refresh_units(conn, units: set[str]) -> dict[str, Decimal]:
    """Force-fetch prices for *units*, bypassing the staleness gate, and upsert
    the cache. Returns the freshly fetched ``{unit: price}`` (units whose fetch
    failed are omitted; the cache is left untouched for those).

    Unlike :func:`get_rates`, this always hits the external API — it backs the
    on-demand "refresh this symbol" control. ``USD`` and blank units are ignored.
    """
    units = {u for u in units if u and u != "USD"}
    if not units:
        return {}
    fetched = _fetch_prices(units)
    if fetched:
        _write_cache(conn, fetched)
    return fetched


def refresh_unit(conn, unit: str) -> Decimal | None:
    """Force-refresh a single symbol (see :func:`refresh_units`).

    Returns the new price, or ``None`` if the fetch failed.
    """
    return refresh_units(conn, {unit}).get(unit)


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------


def _read_cache(conn, units: set[str]) -> dict[str, tuple[Decimal, datetime]]:
    """Read cached prices for the given *units*."""
    rows = conn.execute(
        select(
            price_cache.c.unit, price_cache.c.price_usd, price_cache.c.fetched_at
        ).where(price_cache.c.unit.in_(list(units)))
    ).fetchall()
    result: dict[str, tuple[Decimal, datetime]] = {}
    for row in rows:
        fetched_at = row.fetched_at
        # Ensure fetched_at is timezone-aware for comparison.
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        result[row.unit] = (Decimal(str(row.price_usd)), fetched_at)
    return result


def _write_cache(conn, prices: dict[str, Decimal]) -> None:
    """Upsert cached prices (INSERT OR REPLACE for SQLite)."""
    now = datetime.now(timezone.utc)
    for unit, price in prices.items():
        conn.execute(
            text(
                "INSERT INTO price_cache (unit, price_usd, fetched_at) "
                "VALUES (:unit, :price, :ts) "
                "ON CONFLICT(unit) DO UPDATE SET "
                "price_usd = :price, fetched_at = :ts"
            ),
            {"unit": unit, "price": float(price), "ts": now},
        )
    conn.commit()


# ---------------------------------------------------------------------------
# External API fetchers
# ---------------------------------------------------------------------------


def _fetch_prices(units: set[str]) -> dict[str, Decimal]:
    """Fetch prices for a set of units, dispatching to the right API."""
    crypto = {u for u in units if u in CRYPTO_IDS}
    stocks = units - crypto

    result: dict[str, Decimal] = {}

    if crypto:
        try:
            result.update(_fetch_crypto_prices(crypto))
        except Exception:
            logger.warning(
                "Failed to fetch crypto prices from CoinGecko", exc_info=True
            )

    if stocks:
        try:
            result.update(_fetch_stock_prices(stocks))
        except Exception:
            logger.warning(
                "Failed to fetch stock prices from Yahoo Finance", exc_info=True
            )

    return result


def _fetch_crypto_prices(symbols: set[str]) -> dict[str, Decimal]:
    """Batch-fetch crypto prices from CoinGecko's keyless public API.

    Uses ``GET /api/v3/simple/price?ids=bitcoin,ethereum,...&vs_currencies=usd``
    which returns all requested coins in a single call.
    """
    ids = [CRYPTO_IDS[s] for s in symbols if s in CRYPTO_IDS]
    if not ids:
        return {}

    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={','.join(ids)}&vs_currencies=usd"
    )
    data = _http_get_json(url)
    if data is None:
        return {}

    # Response format: {"bitcoin": {"usd": 67000.12}, "ethereum": {"usd": 3500.45}}
    result: dict[str, Decimal] = {}
    for coin_id, prices in data.items():
        symbol = _ID_TO_SYMBOL.get(coin_id)
        price_val = prices.get("usd") if isinstance(prices, dict) else None
        if symbol and price_val is not None:
            try:
                result[symbol] = Decimal(str(price_val))
            except InvalidOperation:
                logger.warning("Bad price from CoinGecko for %s: %r", symbol, price_val)
    return result


def _fetch_stock_prices(symbols: set[str]) -> dict[str, Decimal]:
    """Fetch stock prices from Yahoo Finance, one ticker at a time.

    Uses the ``/v8/finance/chart/{ticker}`` endpoint which returns
    ``regularMarketPrice`` in the ``meta`` section.
    """
    result: dict[str, Decimal] = {}
    for symbol in symbols:
        try:
            price = _fetch_yahoo_ticker(symbol)
            if price is not None:
                result[symbol] = price
        except Exception:
            logger.warning("Yahoo Finance lookup failed for %s", symbol, exc_info=True)
    return result


def _fetch_yahoo_ticker(symbol: str) -> Decimal | None:
    """Fetch a single ticker's price from Yahoo Finance."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        "?interval=1d&range=1d"
    )
    data = _http_get_json(url)
    if data is None:
        return None
    try:
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        if price is not None:
            return Decimal(str(price))
    except (KeyError, IndexError, TypeError):
        logger.warning("Unexpected Yahoo Finance response for %s", symbol)
    return None


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_get_json(url: str) -> dict | None:
    """GET *url* and parse the JSON body.  Returns ``None`` on any error."""
    req = urllib.request.Request(url, headers={"User-Agent": "fintrack/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("HTTP request failed: %s — %s", url, exc)
        return None
