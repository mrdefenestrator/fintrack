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

Selectable grouping (issue #137) is also shared: a sheet offers a few
``GroupBy`` choices (the first is its default), read from the ``?group=`` URL
param with ``selected_group()`` and picked from the filter bar's "Group by"
dropdown (``filter_controls.group_by_select``). A sheet's own route builds the
bands for each choice; ``band_key()`` and ``category_tones()`` keep their keys
and colors consistent across sheets.

This module holds the Python side the sheets share: turning a column layout
into the per-group header metadata the templates read, mapping a band's
local drag-reorder onto the band's slots in the full, stored ordering, and the
group-by plumbing above.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit

from flask import request

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


class GroupBy(NamedTuple):
    """One "Group by" choice: its ``?group=`` value and its dropdown label."""

    key: str
    label: str


def selected_group(
    options: Sequence[GroupBy], args: Mapping[str, str] | None = None
) -> str:
    """The sheet's active grouping: ``?group=`` if it names one of `options`,
    else the default (the first option). `args` reads another query instead of
    the request's (e.g. the page URL's, for an in-place refresh).

    An edit swap (an htmx cell/row request) doesn't carry the page's query, so
    the param falls back to the page URL htmx sends as ``HX-Current-URL`` —
    the swapped-in body keeps the grouping the page shows."""
    keys = [o.key for o in options]
    if args is not None:
        raw = args.get("group")
        return raw if raw in keys else keys[0]
    raw = request.args.get("group")
    if raw is None and request.headers.get("HX-Request"):
        query = parse_qs(urlsplit(request.headers.get("HX-Current-URL", "")).query)
        raw = (query.get("group") or [None])[0]
    return raw if raw in keys else keys[0]


def band_key(prefix: str, value: str) -> str:
    """A stable DOM/URL-safe key for a band (``data-group``; the per-band sort
    state lives in ``sort_<key>_col/_dir``)."""
    return f"{prefix}-" + (re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "x")


def unique_keys(keys: Iterable[str]) -> list[str]:
    """`keys` with a ``-`` appended to any that repeat (two values that slugify
    alike must still be distinct bands)."""
    seen: set[str] = set()
    out = []
    for key in keys:
        while key in seen:
            key += "-"
        seen.add(key)
        out.append(key)
    return out


# Category colors (partials/grouped_sheet.css palette), cycled in category
# order. Ordered so consecutive hues contrast, including the wrap from last
# to first. Shared so a category reads the same color on every sheet.
CATEGORY_TONES = [
    "blue",
    "amber",
    "violet",
    "emerald",
    "rose",
    "cyan",
    "orange",
    "indigo",
    "lime",
    "fuchsia",
    "teal",
    "pink",
]
# Uncategorized, and the bands of a grouping that isn't by category.
NEUTRAL_TONE = "slate"


def category_order(taxonomy: Sequence[str], used: Iterable[str | None]) -> list[str]:
    """Every category in band order: the taxonomy, then any others in use
    (alphabetically). Uncategorized (empty/None) is left out — it closes the
    sheet."""
    known = set(taxonomy)
    return list(taxonomy) + sorted({c for c in used if c} - known)


def category_tones(categories: Sequence[str]) -> dict[str, str]:
    """Category -> band color, cycling CATEGORY_TONES in `categories` order.
    Colors index the full list, so a category keeps its color while filters
    hide its neighbours."""
    return {
        c: CATEGORY_TONES[i % len(CATEGORY_TONES)] for i, c in enumerate(categories)
    }


UNCATEGORIZED = "Uncategorized"


class Band(NamedTuple):
    """One band of a grouping: its DOM/URL key, heading label, tone, rows."""

    key: str
    label: str
    tone: str
    rows: list


def category_bands(
    items: Iterable[Any],
    category_of: Callable[[Any], str | None],
    taxonomy: Sequence[str],
    prefix: str,
) -> list[Band]:
    """Split `items` into one band per category, in the category taxonomy's
    order (other used categories alphabetically, Uncategorized — no category,
    or the literal "Uncategorized" — last), each in its category's hue and
    Uncategorized neutral. Items keep their order within a band. The shared
    "Group by: Category" of the ledger sheets (Transactions, Merchants,
    Budget vs Actual); Budget builds its own (per-band add/reorder)."""
    by: dict[str, list] = {}
    for item in items:
        by.setdefault(category_of(item) or UNCATEGORIZED, []).append(item)
    named = category_order(taxonomy, (c for c in by if c != UNCATEGORIZED))
    tones = category_tones(named)
    order = [c for c in named if c in by] + (
        [UNCATEGORIZED] if UNCATEGORIZED in by else []
    )
    keys = unique_keys(band_key(prefix, c) for c in order)
    return [Band(k, c, tones.get(c, NEUTRAL_TONE), by[c]) for c, k in zip(order, keys)]
