"""Locations of repo-level config files, resolved relative to the package.

Centralized so the CLI and web apps work from any working directory.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"

CATEGORIES_CONFIG = CONFIGS_DIR / "categories.yaml"
NORMALIZATION_CONFIG = CONFIGS_DIR / "normalization.yaml"
INSTITUTIONS_DIR = CONFIGS_DIR / "institutions"
