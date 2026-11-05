"""Shared plumbing for grouped spreadsheet sheets (Holdings, Budget,
Projections) and their unbanded form (Transactions, Merchants, Categories,
Budget vs Actual).

A grouped sheet is ONE table split into bands. Each band is its own
``<tbody class="sheet-group">`` with a tinted heading row (label + subtotals),
its own sortable column-header row, its data rows, and (in edit mode) an add
row. A trailing tbody holds the pinned master footer. The markup lives in
``templates/partials/grouped_sheet.html``, the look in
``templates/partials/grouped_sheet.css`` (inlined by ``base.html``), per-band
sorting in ``static/js/group-sort.js``.

Banding is optional: a *flat* sheet (``flat_sheet()``) is the same table with
its column header in a ``<thead>`` and ONE untinted body — no heading row, no
accent rail — so a plain list shares the header, row, footer, sorting and
sticky chrome of the banded sheets without inventing a band for it.

This module holds the Python side the sheets share: turning a column layout
into the per-group header metadata the templates read, and mapping a band's
local drag-reorder onto the band's slots in the full, stored ordering.
"""

from collections.abc import Callable, Sequence
from typing import Any

# A column is (key, header, right_align, span).
Column = tuple[str, str, bool, int]


def group_columns(
    cols: Sequence[Column], tooltips: dict[str, str] | None = None
) -> dict[str, Any]:
    """The column-header metadata a group dict carries for the header row
    macro: keys, labels, hover tooltips, right-aligned indices, spans, count."""
    tooltips = tooltips or {}
    return {
        "col_keys": [k for k, _, _, _ in cols],
        "headers": [h for _, h, _, _ in cols],
        "tooltips": [tooltips.get(k, h) for k, h, _, _ in cols],
        "right_align": [i for i, (_, _, ra, _) in enumerate(cols) if ra],
        "spans": [span for _, _, _, span in cols],
        "ncols": len(cols),
    }


def flat_sheet(
    key: str, cols: Sequence[Column], tooltips: dict[str, str] | None = None
) -> dict[str, Any]:
    """Column metadata for an unbanded sheet: one body, keyed ``key`` (the
    per-sheet sort state lives in ``sort_<key>_col/_dir`` URL params), with no
    tone (so no accent rail) and no heading row."""
    return {"key": key, "tone": None, "flat": True, **group_columns(cols, tooltips)}


def parse_order(raw: str) -> list[int] | None:
    """Parse a posted comma-separated ``order`` permutation (None if malformed)."""
    try:
        return [int(x) for x in raw.split(",") if x != ""]
    except ValueError:
        return None


def remap_group_order(
    items: Sequence[Any], in_group: Callable[[Any], bool], local_order: list[int]
) -> list[int] | None:
    """Map a band's local drag permutation onto the full stored ordering.

    A band shows only some rows of its backing table (e.g. the Cash band is a
    slice of ``accounts``; a Budget category band a slice of ``budget_entries``),
    and the client posts a permutation of the band's own 0-based row positions.
    Returns the full-table permutation (new position -> old position) that
    reorders the band's rows among the band's own slots while every other row
    keeps its place, or None if ``local_order`` isn't a permutation of the band.
    """
    positions = [i for i, item in enumerate(items) if in_group(item)]
    if sorted(local_order) != list(range(len(positions))):
        return None
    full = list(range(len(items)))
    for new_local, old_local in enumerate(local_order):
        full[positions[new_local]] = positions[old_local]
    return full
