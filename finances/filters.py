"""Filter helpers (shared by CLI and web)."""

from typing import Any, Dict, List


def filter_accounts_by_type(
    accounts: List[Dict[str, Any]],
    include_types: List[str] | None = None,
    exclude_types: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Filter accounts by type. Empty/None include or exclude means no filter."""
    result = list(accounts)
    if include_types:
        include_set = set(t.lower() for t in include_types)
        result = [a for a in result if (a.get("type") or "").lower() in include_set]
    if exclude_types:
        exclude_set = set(t.lower() for t in exclude_types)
        result = [a for a in result if (a.get("type") or "").lower() not in exclude_set]
    return result


def apply_budget_filters(
    budget: List[Dict[str, Any]],
    include_kinds: List[str] | None = None,
    include_types: List[str] | None = None,
    exclude_types: List[str] | None = None,
    include_recurrence: List[str] | None = None,
    exclude_recurrence: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Apply kind, type, and recurrence filters to unified budget list. Returns filtered list."""
    result = list(budget)
    if include_kinds:
        kinds_set = set(k.lower() for k in include_kinds)
        result = [e for e in result if e.get("kind", "").lower() in kinds_set]
    if include_types:
        include_set = set(include_types)
        result = [e for e in result if e.get("type") in include_set]
    if exclude_types:
        exclude_set = set(exclude_types)
        result = [e for e in result if e.get("type") not in exclude_set]
    if include_recurrence:
        rec_set = set(include_recurrence)
        result = [e for e in result if e.get("recurrence") in rec_set]
    if exclude_recurrence:
        rec_set = set(exclude_recurrence)
        result = [e for e in result if e.get("recurrence") not in rec_set]
    return result


def filter_assets_by_kind(
    assets: List[Dict[str, Any]],
    include_kinds: List[str] | None = None,
) -> List[Dict[str, Any]]:
    """Filter unified assets list by kind. Empty/None include_kinds means no filter."""
    if not include_kinds:
        return list(assets)
    kinds_set = set(k.lower() for k in include_kinds)
    return [e for e in assets if e.get("kind", "").lower() in kinds_set]
