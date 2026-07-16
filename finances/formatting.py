"""Display formatting for amounts, quantities, labels, dates."""

import calendar


def fmt_money(x: float) -> str:
    """Format amount for display; accounting style for negatives (parentheses). Positive gets trailing spaces so length matches negative (for right-align alignment). Zero and negative zero always display as positive $0.00."""
    if x == 0:
        x = 0.0  # normalize -0.0 to positive zero for display
    if x < 0:
        return f"(${abs(x):,.2f})"
    pos = f"${x:,.2f}"
    neg = f"(${abs(x):,.2f})"
    # Pad positive with trailing spaces to match negative length so right-aligned columns align
    return pos + " " * (len(neg) - len(pos))


def fmt_qty(x: float | None) -> str:
    """Format quantity for display: values < 1 as percentage, commas for thousands, no decimals for integers, else all significant decimals (strip trailing zeros)."""
    if x is None:
        return "-"
    if x == 0:
        return "0"
    if abs(x) < 1:
        return f"{x * 100:.4g}%"
    if x == int(x):
        return f"{int(x):,}"
    # Format with enough precision to preserve float, then strip trailing zeros
    s = f"{x:.15g}"
    if "e" in s.lower():
        return s  # scientific notation, leave as-is (or could format differently)
    # Split and add thousands separators to integer part
    if "." in s:
        int_part, dec_part = s.split(".", 1)
        dec_part = dec_part.rstrip("0") or "0"
        int_part = _add_thousands(int_part)
        return f"{int_part}.{dec_part}" if dec_part != "0" else int_part
    return _add_thousands(s)


def _add_thousands(s: str) -> str:
    """Add comma thousands separators to a string of digits (optional leading minus)."""
    if s.startswith("-"):
        return "-" + _add_thousands(s[1:])
    n = len(s)
    if n <= 3:
        return s
    return _add_thousands(s[: n - 3]) + "," + s[n - 3 :]


def fmt_day_ordinal(n: int) -> str:
    """Format day of month for display: 1 -> 1st, 2 -> 2nd, 5 -> 5th, 21 -> 21st."""
    if n in (11, 12, 13):
        return f"{n}th"
    d = n % 10
    if d == 1:
        return f"{n}st"
    if d == 2:
        return f"{n}nd"
    if d == 3:
        return f"{n}rd"
    return f"{n}th"


def fmt_month_short(month: int) -> str:
    """Format month (1-12) as short name: 1 -> Jan, 2 -> Feb, etc."""
    return calendar.month_abbr[month]


def _fmt_label(raw: str | None) -> str:
    """Format a label for display: snake_case -> Title Case; None or '-' -> '-'."""
    if not raw or raw == "-":
        return "-"
    return raw.replace("_", " ").title()


def fmt_type_display(raw: str | None) -> str:
    """Format type for display: credit_card -> Credit card, salary -> Salary."""
    return _fmt_label(raw)


def fmt_recurrence_display(raw: str | None) -> str:
    """Format recurrence for display: one_time -> One time, monthly -> Monthly."""
    return _fmt_label(raw)
