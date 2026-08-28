"""Data-driven "sheet" table schema — the single definition of what goes in a
spreadsheet-style table, so every table is built one way.

Background
----------
The finances/ledger pages used to hand-write their table markup, and two rival
inline-edit macro modules (whole-tbody ``innerHTML`` swaps vs per-row
``outerHTML`` swaps) meant "a table" had no single definition — small changes
rippled into unrelated cells. This module replaces that with a declarative
schema:

* :class:`Column` — one column's static shape (label, kind, alignment, width,
  whether it's editable/sortable, select options, an optional custom editor).
* :class:`Cell` — one rendered cell's dynamic data (display string, edit value,
  per-row editability, per-row select options, colour class).
* :class:`Row` — one row: the ``url_for`` params that identify it plus its
  cells keyed by column key.
* :class:`Group` — a band of rows (flat tables have exactly one implicit group;
  Holdings has four).
* :class:`TableSpec` — the whole table: its DOM id (the htmx swap target),
  columns/flags, and the blueprint endpoint names used to build every per-row
  URL.

Locked vs opt-in
----------------
Styling, the sticky box-shadow chrome, the four scroll drop-shadows, keyboard
navigation and column sorting are **locked ON by default** (they cost nothing
to keep consistent). Row-level behaviours — inline editing, drag-reorder and
delete — are **opt-in flags** because they need matching routes.

URL building
------------
Different tables identify a row differently (Holdings by ``source``+``ref``,
Budget/Categories by ``index``/id, Transactions by row id, …). That whole
difference is captured by :attr:`Row.params` — a dict of ``url_for`` kwargs —
plus :attr:`TableSpec.endpoints` (blueprint route names). The renderer builds
every URL uniformly as
``url_for(spec.endpoints[name], filename=filename[, field=, display=1],
**row.params)``, so no separate "identity" abstraction is needed.

The generic templates ``macros/sheet_table.html`` (page shell) and
``partials/sheet_body.html`` (the swap-target body) consume the context built
by :func:`render_context`; routes call it and hand the result to
``render_template("partials/sheet_body.html", **ctx)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Column kinds. TEXT/NUMBER/CURRENCY/PERCENT/DATE all edit through the text
# input (kind only carries alignment/inputmode intent + documents the column);
# SELECT edits through the dropdown; COMPUTED is never editable (derived value).
KIND_TEXT = "text"
KIND_NUMBER = "number"
KIND_CURRENCY = "currency"
KIND_PERCENT = "percent"
KIND_DATE = "date"
KIND_SELECT = "select"
KIND_COMPUTED = "computed"

_RIGHT_KINDS = {KIND_NUMBER, KIND_CURRENCY, KIND_PERCENT}


@dataclass(frozen=True)
class Column:
    """One column's static shape (shared by every row)."""

    key: str
    label: str = ""
    kind: str = KIND_TEXT
    align: str | None = None  # "left"/"right"; None -> derived from kind
    width: str | None = None  # <col> width, e.g. "10rem"
    editable: bool = False  # schema default; a Cell may still gate per row
    sortable: bool = True  # LOCKED ON by default
    tooltip: str = ""
    colspan: int = 1
    inputmode: str | None = None  # "numeric" | "decimal" | ...
    input_type: str = "text"
    options_key: str | None = None  # static select options via TableSpec.options
    custom_editor: str | None = None  # template name — escape hatch

    @property
    def right(self) -> bool:
        if self.align is not None:
            return self.align == "right"
        return self.kind in _RIGHT_KINDS

    @property
    def is_select(self) -> bool:
        return self.kind == KIND_SELECT


@dataclass
class Cell:
    """One rendered cell's dynamic data."""

    display: str = ""
    raw: str = ""  # value to prefill the editor
    editable: bool = False  # per-row gate (Holdings varies by row)
    options: list[tuple[str, str]] | None = None  # per-row select options
    css: str = ""  # extra colour/muted/staleness classes
    is_negative: bool = False
    sort_value: str | None = None
    colspan: int | None = None  # overrides Column.colspan


@dataclass
class Row:
    """One data row: identity params + cells keyed by column key."""

    params: dict = field(default_factory=dict)  # url_for kwargs
    cells: dict[str, Cell] = field(default_factory=dict)
    dom_id: str = ""  # else derived from spec.row_id_prefix + params
    accent: str = ""  # left-rail accent key (Holdings) or ""
    reorder_index: int | None = None


@dataclass
class Group:
    """A band of rows. Flat tables use a single implicit group (key ``"_"``)."""

    key: str = "_"
    label: str = ""
    columns: list[Column] = field(default_factory=list)  # ragged; else spec.columns
    rows: list[Row] = field(default_factory=list)
    subtotal: str | None = None  # shown in the heading band (grouped tables)
    add_params: dict = field(default_factory=dict)
    add_noun: str = ""
    reorderable: bool = False
    empty_text: str = ""


@dataclass
class TableSpec:
    """The whole table."""

    dom_id: str  # htmx swap target id + data-cell-nav target
    endpoints: dict[str, str] = field(default_factory=dict)
    # keys: cell_edit, update, delete_confirm, delete_btn, delete, reorder, add
    columns: list[Column] = field(default_factory=list)  # flat schema
    grouped: bool = False
    # locked ON:
    column_sort: bool = True
    scroll_shadows: bool = True
    keyboard_nav: bool = True
    sticky_chrome: bool = True
    # opt-in:
    editable: bool = False
    reorderable: bool = False
    deletable: bool = False
    # shared select option lists, keyed by Column.options_key
    options: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    row_id_prefix: str = "row"
    subtotal_col: int = 3  # column index the group subtotal renders in
    heading_label_span: int = 3  # colspan of the group label in the heading band
    colgroup: list[str] | None = None  # explicit <col> widths; else from columns
    footer: list[list[str]] | None = None  # footer/master-footer rows
    footer_amount_pos: int | None = None  # right-aligned col in the footer
    empty_text: str = "Nothing to show."
    # Scroll-container border/rounding. Default borrows its top edge from a
    # filter bar above (left/bottom/right only); a table with no filter bar
    # (e.g. Categories) overrides with all four borders.
    container_class: str = (
        "rounded-b-lg border-l border-b border-r "
        "border-gray-300 dark:border-gray-600 shadow-sm"
    )


def _group_ncols(group: Group, spec: TableSpec) -> int:
    cols = group.columns or spec.columns
    return sum(c.colspan for c in cols)


def _effective_groups(spec: TableSpec, groups: list[Group] | None) -> list[Group]:
    """The groups to render. A flat spec synthesises a single group over
    ``spec.columns`` so the renderer has exactly one code path."""
    if groups is not None:
        return groups
    return [Group(key="_", columns=spec.columns, rows=[])]


def max_cols(spec: TableSpec, groups: list[Group]) -> int:
    """Widest group's total column count (drives ragged-right filler cells)."""
    return max((_group_ncols(g, spec) for g in groups), default=0)


def row_dom_id(spec: TableSpec, row: Row) -> str:
    """Stable per-row DOM id. Explicit ``row.dom_id`` wins; otherwise it's the
    prefix joined with the identity params (e.g. ``row-account-5``)."""
    if row.dom_id:
        return row.dom_id
    parts = [spec.row_id_prefix, *[str(v) for v in row.params.values()]]
    return "-".join(parts)


def render_context(
    spec: TableSpec,
    groups: list[Group] | None = None,
    *,
    filename: str | None = None,
    editing: dict | None = None,
    updated: dict | None = None,
    error: str | None = None,
) -> dict:
    """Build the context for ``partials/sheet_body.html``.

    ``editing``/``updated`` are ``{"params": {...}, "field": "..."}`` (or None):
    the renderer puts exactly that cell into edit / just-updated state by
    matching ``row.params`` + column key.
    """
    effective = _effective_groups(spec, groups)
    return {
        "spec": spec,
        "groups": effective,
        "filename": filename,
        "editing": editing,
        "updated": updated,
        "error": error,
        "max_cols": max_cols(spec, effective),
        # helpers the template calls
        "row_dom_id": row_dom_id,
        "group_ncols": _group_ncols,
    }
