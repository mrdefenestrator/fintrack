"""Shared CRUD helpers for web routes.

Replaces duplicated type coercion, update, delete, and move logic
across accounts, budget, and assets routes.
"""

from decimal import Decimal, InvalidOperation

from flask import current_app, request

# =============================================================================
# Field coercion maps: field name -> coercion type
# =============================================================================

DECIMAL_COERCE = "decimal"
INT_COERCE = "int"

ACCOUNTS_COERCION = {
    "balance": DECIMAL_COERCE,
    "limit": DECIMAL_COERCE,
    "available": DECIMAL_COERCE,
    "rewards_balance": DECIMAL_COERCE,
    "statement_balance": DECIMAL_COERCE,
    "minimum_balance": DECIMAL_COERCE,
    "statement_due_day_of_month": INT_COERCE,
    "paymentAccountRef": INT_COERCE,
}

BUDGET_COERCION = {
    "amount": DECIMAL_COERCE,
    "dayOfMonth": INT_COERCE,
    "month": INT_COERCE,
    "dayOfYear": INT_COERCE,
    "autoAccountRef": INT_COERCE,
}

ASSETS_COERCION = {
    "value": DECIMAL_COERCE,
    "quantity": DECIMAL_COERCE,
    "balance": DECIMAL_COERCE,
    "interestRate": DECIMAL_COERCE,
    "annualReturnRate": DECIMAL_COERCE,
    "monthlyContribution": DECIMAL_COERCE,
    "originalPrincipal": DECIMAL_COERCE,
    "termMonths": INT_COERCE,
    "statement_due_day_of_month": INT_COERCE,
    "assetRef": INT_COERCE,
}


def coerce_value(field: str, value_raw: str, coercion_map: dict):
    """Coerce a raw string value to the correct Python type based on field.

    Returns (value, error). If error is not None, coercion failed.
    """
    coerce_type = coercion_map.get(field)
    if coerce_type == DECIMAL_COERCE:
        try:
            return (Decimal(value_raw) if value_raw else None), None
        except InvalidOperation:
            return None, f"Invalid number for {field}"
    elif coerce_type == INT_COERCE:
        try:
            return (int(value_raw) if value_raw else None), None
        except ValueError:
            return None, f"Invalid integer for {field}"
    return value_raw, None


def handle_delete(writer_fn, engine):
    """Generic delete handler.

    Args:
        writer_fn: callable(conn) -> None, may raise ValueError
        engine: SQLAlchemy Engine
    """
    try:
        with engine.connect() as conn:
            writer_fn(conn)
    except ValueError:
        return "", 422
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


def handle_move(writer_fn, engine):
    """Generic move handler.

    Args:
        writer_fn: callable(conn, direction) -> None, may raise ValueError
        engine: SQLAlchemy Engine
    """
    direction = request.args.get("direction", "up").lower()
    if direction not in ("up", "down"):
        return "", 422
    try:
        with engine.connect() as conn:
            writer_fn(conn, direction)
    except ValueError:
        return "", 422
    resp = current_app.make_response("")
    resp.headers["HX-Refresh"] = "true"
    return resp


def handle_reorder(writer_fn, engine):
    """Generic drag-reorder handler (QA item 3).

    Reads a comma-separated `order` form field (a permutation of the rows'
    current 0-based positions) and persists it. Returns 204 with no body: the
    client (SortableJS) has already moved the DOM into the new order, so there
    is nothing to swap and no jarring refresh.

    Args:
        writer_fn: callable(conn, new_order: list[int]) -> None, may raise ValueError
        engine: SQLAlchemy Engine
    """
    raw = request.form.get("order", "")
    try:
        new_order = [int(x) for x in raw.split(",") if x != ""]
    except ValueError:
        return "", 422
    if not new_order:
        return "", 422
    try:
        with engine.connect() as conn:
            writer_fn(conn, new_order)
    except ValueError:
        return "", 422
    return "", 204
