"""Filter helpers (shared by CLI and web)."""

from typing import Any


def filter_accounts_by_type(
    accounts: list[dict[str, Any]],
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter accounts by type. Empty/None include or exclude means no filter."""
    result = list(accounts)
    if include_types:
        include_set = {t.lower() for t in include_types}
        result = [a for a in result if (a.get("type") or "").lower() in include_set]
    if exclude_types:
        exclude_set = {t.lower() for t in exclude_types}
        result = [a for a in result if (a.get("type") or "").lower() not in exclude_set]
    return result


def apply_budget_filters(
    budget: list[dict[str, Any]],
    include_kinds: list[str] | None = None,
    include_categories: list[str] | None = None,
    exclude_categories: list[str] | None = None,
    include_recurrence: list[str] | None = None,
    exclude_recurrence: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Apply kind, category, and recurrence filters to unified budget list."""
    result = list(budget)
    if include_kinds:
        kinds_set = {k.lower() for k in include_kinds}
        result = [e for e in result if e.get("kind", "").lower() in kinds_set]
    if include_categories:
        include_set = set(include_categories)
        result = [e for e in result if e.get("category") in include_set]
    if exclude_categories:
        exclude_set = set(exclude_categories)
        result = [e for e in result if e.get("category") not in exclude_set]
    if include_recurrence:
        rec_set = set(include_recurrence)
        result = [e for e in result if e.get("recurrence") in rec_set]
    if exclude_recurrence:
        rec_set = set(exclude_recurrence)
        result = [e for e in result if e.get("recurrence") not in rec_set]
    return result


def filter_assets_by_kind(
    assets: list[dict[str, Any]],
    include_kinds: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter unified assets list by kind. Empty/None include_kinds means no filter."""
    if not include_kinds:
        return list(assets)
    kinds_set = {k.lower() for k in include_kinds}
    return [e for e in assets if e.get("kind", "").lower() in kinds_set]
