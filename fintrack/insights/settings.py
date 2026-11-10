"""Overview insight thresholds (issue #102), tuned in ``configs/insights.yaml``."""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType

import yaml

from fintrack.core.config import INSIGHTS_CONFIG

GROUPS = ("cash", "credit_card", "loan", "asset")


def _per_group(cash: int, credit_card: int, loan: int, asset: int):
    values = {"cash": cash, "credit_card": credit_card, "loan": loan, "asset": asset}
    return field(default_factory=lambda: MappingProxyType(dict(values)))


@dataclass(frozen=True)
class InsightsConfig:
    """Insight thresholds; configs/insights.yaml overrides any of these."""

    # Days after which a holding's balance is stale (amber), per group.
    stale_days: Mapping[str, int] = _per_group(35, 35, 90, 90)
    # Days after which it is very stale (red), per group.
    very_stale_days: Mapping[str, int] = _per_group(95, 95, 180, 180)
    # Days since an account's last confirmed import before a reminder.
    import_stale_days: int = 35


_PER_GROUP_KEYS = ("stale_days", "very_stale_days")


def load_insights_config(path: Path | str = INSIGHTS_CONFIG) -> InsightsConfig:
    """InsightsConfig from YAML; a missing file means all defaults. Unknown
    keys (or groups) are an error, so a typo doesn't silently fall back."""
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return InsightsConfig()
    known = {f.name for f in fields(InsightsConfig)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"Unknown insight settings in {path}: {sorted(unknown)}")
    defaults = InsightsConfig()
    for key in _PER_GROUP_KEYS:
        if key not in raw:
            continue
        given = raw[key] or {}
        bad = set(given) - set(GROUPS)
        if bad:
            raise ValueError(f"Unknown groups for {key} in {path}: {sorted(bad)}")
        raw[key] = MappingProxyType({**getattr(defaults, key), **given})
    return InsightsConfig(**raw)
