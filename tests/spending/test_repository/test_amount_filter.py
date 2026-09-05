"""Unit tests for the amount-search parser used by the transactions filter.

parse_amount_filter lives in fintrack/core/coerce.py (alongside other
read/write coercion helpers) so it stays a pure function the repository can
apply -- no Flask/request dependency.
"""

from decimal import Decimal

from fintrack.core.coerce import AMOUNT_FILTER_TOLERANCE, parse_amount_filter


def test_plain_number_is_sign_insensitive_tolerance():
    spec = parse_amount_filter("12.34")
    assert spec.kind == "tolerance"
    assert spec.value == Decimal("12.34")
    assert spec.sign is None


def test_negative_number_is_sign_sensitive_negative():
    spec = parse_amount_filter("-12.34")
    assert spec.kind == "tolerance"
    assert spec.value == Decimal("12.34")
    assert spec.sign == "neg"


def test_positive_number_is_sign_sensitive_positive():
    spec = parse_amount_filter("+12.34")
    assert spec.kind == "tolerance"
    assert spec.value == Decimal("12.34")
    assert spec.sign == "pos"


def test_integer_input_works_too():
    spec = parse_amount_filter("50")
    assert spec.kind == "tolerance"
    assert spec.value == Decimal(50)
    assert spec.sign is None


def test_dash_range():
    spec = parse_amount_filter("10-20")
    assert spec.kind == "range"
    assert spec.low == Decimal(10)
    assert spec.high == Decimal(20)


def test_dotdot_range():
    spec = parse_amount_filter("10..20")
    assert spec.kind == "range"
    assert spec.low == Decimal(10)
    assert spec.high == Decimal(20)


def test_range_with_decimals():
    spec = parse_amount_filter("10.5-20.25")
    assert spec.kind == "range"
    assert spec.low == Decimal("10.5")
    assert spec.high == Decimal("20.25")


def test_range_normalizes_reversed_bounds():
    spec = parse_amount_filter("20-10")
    assert spec.kind == "range"
    assert spec.low == Decimal(10)
    assert spec.high == Decimal(20)


def test_range_tolerates_whitespace():
    spec = parse_amount_filter(" 10 - 20 ")
    assert spec.kind == "range"
    assert spec.low == Decimal(10)
    assert spec.high == Decimal(20)


def test_greater_than():
    spec = parse_amount_filter(">50")
    assert spec.kind == "gt"
    assert spec.value == Decimal(50)


def test_greater_than_or_equal():
    spec = parse_amount_filter(">=50")
    assert spec.kind == "gte"
    assert spec.value == Decimal(50)


def test_less_than():
    spec = parse_amount_filter("<25")
    assert spec.kind == "lt"
    assert spec.value == Decimal(25)


def test_less_than_or_equal():
    spec = parse_amount_filter("<=25")
    assert spec.kind == "lte"
    assert spec.value == Decimal(25)


def test_comparison_tolerates_whitespace():
    spec = parse_amount_filter(" >= 25 ")
    assert spec.kind == "gte"
    assert spec.value == Decimal(25)


def test_none_input_is_ignored():
    assert parse_amount_filter(None) is None


def test_empty_string_is_ignored():
    assert parse_amount_filter("") is None


def test_whitespace_only_is_ignored():
    assert parse_amount_filter("   ") is None


def test_garbage_text_is_ignored():
    assert parse_amount_filter("abc") is None


def test_multiple_signs_is_ignored():
    assert parse_amount_filter("--12") is None


def test_malformed_comparison_is_ignored():
    assert parse_amount_filter(">") is None
    assert parse_amount_filter("> ") is None


def test_stray_operators_is_ignored():
    assert parse_amount_filter("12.34.56") is None
    assert parse_amount_filter("$12.34") is None
    assert parse_amount_filter("12,345") is None


def test_tolerance_constant_is_half_dollar():
    assert AMOUNT_FILTER_TOLERANCE == Decimal("0.50")
