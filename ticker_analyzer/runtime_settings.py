"""Environment settings used consistently by UI and background actions."""

from __future__ import annotations

import os


def setting_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def mutation_allowed(setting: str) -> bool:
    return os.getenv("APP_MODE", "local").strip().lower() != "production" or setting_enabled(setting)
