"""The Type / Balance / Institution filters shared by the holding sheets
(Holdings and Projections, #144).

A sheet hands over its rows as dicts carrying three keys — ``type_value`` (the
holding type, or None), ``balance_side`` ("asset" / "liability", by the
holding's group, not the sign of its amount) and ``institution`` ("" when
unset) — and gets back the active selection, the filtered rows, and the
dropdown options for ``filter_controls.multi_select``. The selections come
from the repeated ``?type=`` / ``?balance=`` / ``?institution=`` URL params and
keep only values that some row actually has.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fintrack.core.types import HOLDING_TYPE_OPTIONS

# The two "balance side" buckets: assets add to net worth, liabilities subtract.
BALANCE_LABELS: dict[str, str] = {"asset": "Assets", "liability": "Liabilities"}

# The URL params the filters read (also what a sheet's links must carry).
FILTER_PARAMS = ("type", "balance", "institution")


@dataclass(frozen=True)
class HoldingFilters:
    type_sel: list[str]
    balance_sel: list[str]
    inst_sel: list[str]
    type_options: list[tuple[str, str]]
    institutions: list[str]

    @property
    def active(self) -> bool:
        """Any filter narrowing the rows."""
        return bool(self.type_sel or self.balance_sel or self.inst_sel)

    @property
    def active_count(self) -> int:
        """Filters that hide something (all-ticked counts as off), for the
        filter bar's badge and Reset."""

        def one(sel: Sequence[str], total: int) -> int:
            return len(sel) if 0 < len(sel) < total else 0

        return (
            one(self.type_sel, len(self.type_options))
            + one(self.balance_sel, len(BALANCE_LABELS))
            + one(self.inst_sel, len(self.institutions))
        )

    def matches(self, row: Mapping[str, Any]) -> bool:
        return (
            (not self.type_sel or row["type_value"] in self.type_sel)
            and (not self.balance_sel or row["balance_side"] in self.balance_sel)
            and (not self.inst_sel or row["institution"] in self.inst_sel)
        )

    def apply(self, rows: Iterable[Mapping[str, Any]]) -> list:
        return [r for r in rows if self.matches(r)]

    def params(self) -> dict[str, list[str] | None]:
        """The selections as URL params (None when unset), for a sheet's links."""
        return {
            "type": self.type_sel or None,
            "balance": self.balance_sel or None,
            "institution": self.inst_sel or None,
        }

    def context(self) -> dict[str, Any]:
        """Template context: the three dropdowns' options and the badge count."""

        def opts(values_labels, selected):
            return [
                {"value": v, "display": lbl, "checked": v in selected}
                for v, lbl in values_labels
            ]

        return {
            "active_count": self.active_count,
            "type_opts": opts(self.type_options, self.type_sel),
            "balance_opts": opts(list(BALANCE_LABELS.items()), self.balance_sel),
            "institution_opts": opts(
                [(i, i) for i in self.institutions], self.inst_sel
            ),
        }


def read_filters(rows: Iterable[Mapping[str, Any]], args: Any) -> HoldingFilters:
    """The filters `args` (a request's MultiDict) selects over `rows`. Options
    list only the types and institutions present in `rows`."""
    rows = list(rows)
    present_types = {r["type_value"] for r in rows if r["type_value"]}
    type_options = [(v, lbl) for v, lbl in HOLDING_TYPE_OPTIONS if v in present_types]
    institutions = sorted({r["institution"] for r in rows if r["institution"]})
    return HoldingFilters(
        type_sel=[t for t in args.getlist("type") if t in present_types],
        balance_sel=[b for b in args.getlist("balance") if b in BALANCE_LABELS],
        inst_sel=[i for i in args.getlist("institution") if i in institutions],
        type_options=type_options,
        institutions=institutions,
    )
