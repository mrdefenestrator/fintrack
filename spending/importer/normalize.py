import re
from functools import lru_cache

import yaml

DEFAULT_CONFIG = "configs/normalization.yaml"


@lru_cache(maxsize=1)
def _load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def normalize_merchant(raw_description: str, config_path: str = DEFAULT_CONFIG) -> str:
    config = _load_config(config_path)
    text = raw_description.upper().strip()

    # Strip known prefixes (exact match at start)
    for prefix in config.get("prefixes", []):
        if text.startswith(prefix.upper()):
            text = text[len(prefix) :]
            break

    # Strip leading patterns (loop until stable)
    leading_patterns = config.get("leading_patterns", [])
    changed = True
    while changed:
        prev = text
        for pattern in leading_patterns:
            text = re.sub(pattern, "", text)
        changed = text != prev

    # Strip trailing patterns (loop until stable to handle compound suffixes)
    trailing_patterns = config.get("trailing_patterns", [])
    changed = True
    while changed:
        prev = text
        for pattern in trailing_patterns:
            text = re.sub(pattern, "", text)
        changed = text != prev

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # Apply merchant aliases (exact match on fully-stripped name)
    aliases = {k.upper(): v.upper() for k, v in config.get("aliases", {}).items()}
    text = aliases.get(text, text)

    return text
