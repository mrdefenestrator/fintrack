"""Tests for fintrack.networth.prices — cache I/O, staleness, API fallback."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from fintrack.core.models import metadata
from fintrack.networth.prices import (
    STALENESS_THRESHOLD,
    _read_cache,
    _write_cache,
    get_price_meta,
    get_rates,
    refresh_unit,
    refresh_units,
)


@pytest.fixture()
def conn():
    """In-memory SQLite database with the price_cache table."""
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with engine.connect() as connection:
        yield connection


# ---------------------------------------------------------------------------
# _read_cache / _write_cache
# ---------------------------------------------------------------------------


def test_write_and_read_cache(conn):
    prices = {"BTC": Decimal("60000.12"), "ETH": Decimal("3500.50")}
    _write_cache(conn, prices)
    cached = _read_cache(conn, {"BTC", "ETH"})
    assert "BTC" in cached
    assert "ETH" in cached
    assert cached["BTC"][0] == Decimal("60000.12")
    assert cached["ETH"][0] == Decimal("3500.50")
    # fetched_at should be recent
    now = datetime.now(timezone.utc)
    assert abs((now - cached["BTC"][1]).total_seconds()) < 5


def test_read_cache_missing_unit(conn):
    """Reading a unit that doesn't exist returns empty."""
    result = _read_cache(conn, {"MISSING"})
    assert result == {}


def test_write_cache_upsert(conn):
    """Writing the same unit twice updates the price."""
    _write_cache(conn, {"BTC": Decimal("50000")})
    _write_cache(conn, {"BTC": Decimal("60000")})
    cached = _read_cache(conn, {"BTC"})
    assert cached["BTC"][0] == Decimal("60000")


# ---------------------------------------------------------------------------
# get_rates — cache-only scenarios (no external API calls)
# ---------------------------------------------------------------------------


def test_get_rates_fresh_cache_no_fetch(conn):
    """When the cache is fresh (< STALENESS_THRESHOLD), no fetch is attempted."""
    _write_cache(conn, {"BTC": Decimal("60000")})
    with patch("fintrack.networth.prices._fetch_prices") as mock_fetch:
        rates = get_rates(conn, {"BTC"})
    mock_fetch.assert_not_called()
    assert rates["BTC"] == Decimal("60000")


def test_get_rates_stale_triggers_fetch(conn):
    """When the cache is stale, _fetch_prices is called."""
    # Write a stale entry
    stale_time = datetime.now(timezone.utc) - STALENESS_THRESHOLD - timedelta(hours=1)
    conn.execute(
        text(
            "INSERT INTO price_cache (unit, price_usd, fetched_at) "
            "VALUES (:unit, :price, :ts)"
        ),
        {"unit": "BTC", "price": 50000.0, "ts": stale_time},
    )
    conn.commit()

    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal("62000")},
    ) as mock_fetch:
        rates = get_rates(conn, {"BTC"})
    mock_fetch.assert_called_once()
    assert rates["BTC"] == Decimal("62000")


def test_get_rates_fetch_failure_uses_stale_cache(conn):
    """When fetch fails, the stale cached value is still returned."""
    stale_time = datetime.now(timezone.utc) - STALENESS_THRESHOLD - timedelta(hours=1)
    conn.execute(
        text(
            "INSERT INTO price_cache (unit, price_usd, fetched_at) "
            "VALUES (:unit, :price, :ts)"
        ),
        {"unit": "ETH", "price": 3000.0, "ts": stale_time},
    )
    conn.commit()

    with patch("fintrack.networth.prices._fetch_prices", return_value={}):
        rates = get_rates(conn, {"ETH"})
    # Stale value is returned as fallback
    assert rates["ETH"] == Decimal("3000")


def test_get_rates_no_cache_no_fetch_returns_empty(conn):
    """When there's no cache and fetch returns nothing, unit is omitted."""
    with patch("fintrack.networth.prices._fetch_prices", return_value={}):
        rates = get_rates(conn, {"UNKNOWN_TICKER"})
    assert "UNKNOWN_TICKER" not in rates


def test_get_rates_empty_units(conn):
    """Passing empty units returns empty dict without any fetch."""
    with patch("fintrack.networth.prices._fetch_prices") as mock_fetch:
        rates = get_rates(conn, set())
    mock_fetch.assert_not_called()
    assert rates == {}


# ---------------------------------------------------------------------------
# _fetch_prices dispatch (mock HTTP)
# ---------------------------------------------------------------------------


def _mock_http_get_json(url):
    """Mock HTTP responses for CoinGecko and Yahoo Finance."""
    if "coingecko.com" in url:
        return {
            "bitcoin": {"usd": 61234.56},
            "ethereum": {"usd": 3456.78},
        }
    if "yahoo" in url:
        if "AAPL" in url:
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 175.25}}]}}
    return None


def test_fetch_crypto_prices():
    """CoinGecko response is parsed correctly."""
    from fintrack.networth.prices import _fetch_crypto_prices

    with patch(
        "fintrack.networth.prices._http_get_json", side_effect=_mock_http_get_json
    ):
        result = _fetch_crypto_prices({"BTC", "ETH"})
    assert result["BTC"] == Decimal("61234.56")
    assert result["ETH"] == Decimal("3456.78")


def test_fetch_stock_prices():
    """Yahoo Finance response is parsed correctly."""
    from fintrack.networth.prices import _fetch_stock_prices

    with patch(
        "fintrack.networth.prices._http_get_json", side_effect=_mock_http_get_json
    ):
        result = _fetch_stock_prices({"AAPL"})
    assert result["AAPL"] == Decimal("175.25")


def test_fetch_prices_api_failure_logged():
    """API failure returns empty dict without crashing."""
    from fintrack.networth.prices import _fetch_prices

    with patch("fintrack.networth.prices._http_get_json", return_value=None):
        result = _fetch_prices({"BTC", "AAPL"})
    # CoinGecko returns None → empty crypto result; Yahoo returns None → empty stock
    assert result == {}


# ---------------------------------------------------------------------------
# get_price_meta — freshness for the UI
# ---------------------------------------------------------------------------


def test_get_price_meta_returns_fetched_at(conn):
    _write_cache(conn, {"BTC": Decimal("60000")})
    meta = get_price_meta(conn, {"BTC"})
    assert "BTC" in meta
    assert abs((datetime.now(timezone.utc) - meta["BTC"]).total_seconds()) < 5


def test_get_price_meta_omits_uncached_and_never_fetches(conn):
    with patch("fintrack.networth.prices._fetch_prices") as mock_fetch:
        meta = get_price_meta(conn, {"MISSING"})
    mock_fetch.assert_not_called()
    assert meta == {}


def test_get_price_meta_empty_units(conn):
    assert get_price_meta(conn, set()) == {}


# ---------------------------------------------------------------------------
# refresh_units / refresh_unit — force-refresh bypassing the staleness gate
# ---------------------------------------------------------------------------


def test_refresh_units_force_fetches_even_when_fresh(conn):
    """A just-cached price is still refetched — refresh is on-demand, not staleness-gated."""
    _write_cache(conn, {"BTC": Decimal("60000")})
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"BTC": Decimal("61234.56")},
    ) as mock_fetch:
        result = refresh_units(conn, {"BTC"})
    mock_fetch.assert_called_once()
    assert result == {"BTC": Decimal("61234.56")}
    # Cache reflects the new price.
    assert _read_cache(conn, {"BTC"})["BTC"][0] == Decimal("61234.56")


def test_refresh_unit_returns_new_price(conn):
    with patch(
        "fintrack.networth.prices._fetch_prices",
        return_value={"ETH": Decimal("3456.78")},
    ):
        assert refresh_unit(conn, "ETH") == Decimal("3456.78")


def test_refresh_units_ignores_usd_and_blank(conn):
    with patch("fintrack.networth.prices._fetch_prices") as mock_fetch:
        result = refresh_units(conn, {"USD", ""})
    mock_fetch.assert_not_called()
    assert result == {}


def test_refresh_unit_failure_leaves_cache_untouched(conn):
    _write_cache(conn, {"BTC": Decimal("60000")})
    with patch("fintrack.networth.prices._fetch_prices", return_value={}):
        assert refresh_unit(conn, "BTC") is None
    # The stale cache value survives a failed refresh.
    assert _read_cache(conn, {"BTC"})["BTC"][0] == Decimal("60000")
