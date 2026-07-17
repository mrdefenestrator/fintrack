"""Value coercion at repository read/write boundaries."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


def to_date(value) -> date | None:
    """Coerce a repository input value to a date.

    Accepts date objects (passed through), ISO-8601 strings, empty string /
    None (both become None). Raises ValueError for unparseable strings.
    """
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


# Tolerance (in dollars) applied to "12.34"-style single-value amount
# searches, so both legs of a transfer (e.g. -$500 / +$500) surface even
# when statement fees/rounding nudge one side.
AMOUNT_FILTER_TOLERANCE = Decimal("0.50")

_NUM = r"\d+(?:\.\d+)?"
_COMPARISON_RE = re.compile(rf"^\s*(>=|<=|>|<)\s*({_NUM})\s*$")
_RANGE_RE = re.compile(rf"^\s*({_NUM})\s*(?:-|\.\.)\s*({_NUM})\s*$")
_SIGNED_RE = re.compile(rf"^\s*([+-])\s*({_NUM})\s*$")
_PLAIN_RE = re.compile(rf"^\s*({_NUM})\s*$")

_COMPARISON_KINDS = {"<": "lt", "<=": "lte", ">": "gt", ">=": "gte"}


@dataclass(frozen=True)
class AmountFilterSpec:
    """Structured amount-search filter, ready for a repository to apply.

    kind is one of: "tolerance", "range", "gt", "gte", "lt", "lte".
      - "tolerance": match abs(amount) within AMOUNT_FILTER_TOLERANCE of
        `value`; `sign` is "neg"/"pos" to additionally require the raw
        (signed) amount be negative/positive, or None for sign-insensitive.
      - "range": match abs(amount) between `low` and `high` inclusive.
      - "gt"/"gte"/"lt"/"lte": compare abs(amount) against `value`.
    """

    kind: str
    value: Decimal | None = None
    low: Decimal | None = None
    high: Decimal | None = None
    sign: str | None = None


def parse_amount_filter(raw: str | None) -> AmountFilterSpec | None:
    """Parse a user-entered amount-search expression.

    Accepted syntax (whitespace-tolerant):
      "12.34"          -> abs(amount) within +/-0.50 of 12.34, either sign
      "-12.34"         -> as above, but amount must be negative
      "+12.34"         -> as above, but amount must be positive
      "10-20", "10..20" -> abs(amount) in [10, 20]
      ">50", "<25", ">=10", "<=5" -> abs(amount) comparison

    Invalid or empty input is never an error -- it simply means "no
    filter", so callers should treat None as "don't filter".
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None

    m = _COMPARISON_RE.match(text)
    if m:
        op, value_str = m.groups()
        try:
            return AmountFilterSpec(
                kind=_COMPARISON_KINDS[op], value=Decimal(value_str)
            )
        except InvalidOperation:
            return None

    m = _RANGE_RE.match(text)
    if m:
        low_str, high_str = m.groups()
        try:
            low, high = Decimal(low_str), Decimal(high_str)
        except InvalidOperation:
            return None
        if low > high:
            low, high = high, low
        return AmountFilterSpec(kind="range", low=low, high=high)

    m = _SIGNED_RE.match(text)
    if m:
        sign, value_str = m.groups()
        try:
            value = Decimal(value_str)
        except InvalidOperation:
            return None
        return AmountFilterSpec(
            kind="tolerance", value=value, sign="neg" if sign == "-" else "pos"
        )

    m = _PLAIN_RE.match(text)
    if m:
        try:
            value = Decimal(m.group(1))
        except InvalidOperation:
            return None
        return AmountFilterSpec(kind="tolerance", value=value, sign=None)

    return None
