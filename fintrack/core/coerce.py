"""Value coercion at repository write boundaries."""

from datetime import date


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
