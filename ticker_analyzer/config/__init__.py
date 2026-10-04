from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from ticker_analyzer.config.defaults import (
    CALIBRATION_VERSION,
    DEFAULT_FULL_CONFIDENCE_COVERAGE,
    DEFAULT_MINIMUM_COVERAGE,
    DEFAULT_MISSING_POLICY,
    LEGACY_METRIC_IDS,
    SCORING_MODEL,
    default_data_quality_config,
    default_profile_overrides,
    default_profile_rules,
    ensure_v4_defaults,
    ensure_v5_defaults,
    financial_groups,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    reconcile_v3_groups,
    specialized_financial_profiles,
)
from ticker_analyzer.config.validation import (
    ConfigValidationError as ConfigValidationError,
)
from ticker_analyzer.config.validation import (
    _validate_metric_list as _validate_metric_list,
)
from ticker_analyzer.config.validation import (
    optional_number as optional_number,
)
from ticker_analyzer.config.validation import (
    validate_all_groups as validate_all_groups,
)
from ticker_analyzer.config.validation import (
    validate_config as validate_config,
)
from ticker_analyzer.config.validation import (
    validate_coverage_policy as validate_coverage_policy,
)
from ticker_analyzer.config.validation import (
    validate_data_quality as validate_data_quality,
)
from ticker_analyzer.config.validation import (
    validate_groups as validate_groups,
)
from ticker_analyzer.config.validation import (
    validate_groups_for_tabs as validate_groups_for_tabs,
)
from ticker_analyzer.config.validation import (
    validate_metric as validate_metric,
)
from ticker_analyzer.config.validation import (
    validate_minimum_coverage as validate_minimum_coverage,
)
from ticker_analyzer.config.validation import (
    validate_missing_policy as validate_missing_policy,
)
from ticker_analyzer.config.validation import (
    validate_profile_metrics as validate_profile_metrics,
)
from ticker_analyzer.config.validation import (
    validate_profile_rules as validate_profile_rules,
)
from ticker_analyzer.config.validation import (
    validate_tab_weights as validate_tab_weights,
)
from ticker_analyzer.config.validation import (
    validate_thresholds as validate_thresholds,
)
from ticker_analyzer.file_io import write_json_atomic
from ticker_analyzer.numbers import clean_number as clean_number

CONFIG_PATH = Path("metrics_config.json")
CONFIG_VERSION = 5
SUPPORTED_CONFIG_VERSIONS = {3, 4, 5}

__all__ = [
    "CONFIG_PATH",
    "CONFIG_VERSION",
    "ConfigValidationError",
    "MetricsConfig",
    "load_config",
    "save_config",
    "normalize_config",
    "validate_config",
    "migrate_v3_to_v4",
    "migrate_v4_to_v5",
    "reconcile_v3_groups",
    "ensure_v4_defaults",
    "ensure_v5_defaults",
    "specialized_financial_profiles",
    "financial_groups",
    "default_profile_rules",
    "default_profile_overrides",
    "default_data_quality_config",
]


class MetricsConfig:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> MetricsConfig:
        return cls(load_config(path))

    def save(self, path: Path = CONFIG_PATH) -> None:
        save_config(self.data, path)

    @property
    def metrics(self) -> dict[str, list[dict[str, Any]]]:
        return self.data.get("metrics", {})

    @property
    def tab_weights(self) -> dict[str, Any]:
        return self.data.get("tab_weights", {})

    @property
    def rating_thresholds(self) -> dict[str, Any]:
        return self.data.get("rating_thresholds", {})


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    stat = path.stat()
    normalized = _load_config_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    # Callers historically receive an independent mutable config. Keep that
    # contract while reusing the expensive parsing, migration and validation.
    return deepcopy(normalized)


@lru_cache(maxsize=1)
def _load_config_cached(resolved_path: str, modified_ns: int, size: int) -> dict[str, Any]:
    del modified_ns, size  # Cache-key fields; reading only needs the resolved path.
    with Path(resolved_path).open("r", encoding="utf-8") as handle:
        return normalize_config(json.load(handle))


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    write_json_atomic(normalize_config(config), path, durable=True)
    _load_config_cached.cache_clear()


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigValidationError("Configuration must be a JSON object.")
    normalized = deepcopy(config)
    version = int(optional_number(normalized.get("version", CONFIG_VERSION)) or CONFIG_VERSION)
    if version == 2:
        raise ConfigValidationError(
            "Config v2 uses pre-anchor scoring semantics. Run scripts/migrate_config.py before loading."
        )
    if version not in SUPPORTED_CONFIG_VERSIONS:
        raise ConfigValidationError(f"Unsupported configuration version: {version}")
    if version == 3:
        normalized = migrate_v3_to_v4(normalized)
        version = 4
    if version == 4:
        normalized = migrate_v4_to_v5(normalized)
    normalized["version"] = CONFIG_VERSION
    normalized.setdefault("scoring_model", SCORING_MODEL)
    normalized.setdefault("calibration_version", CALIBRATION_VERSION)
    normalized.setdefault("tab_weights", {})
    normalized.setdefault("rating_thresholds", {})
    normalized.setdefault("overall_rating_labels", {})
    normalized.setdefault("tab_rating_labels", {})
    normalized.setdefault("tab_rating_thresholds", {})
    normalized.setdefault("missing_policy", deepcopy(DEFAULT_MISSING_POLICY))
    normalized.setdefault("minimum_weight_coverage", deepcopy(DEFAULT_MINIMUM_COVERAGE))
    normalized.setdefault(
        "coverage_policy",
        {
            "minimum_to_score": deepcopy(normalized["minimum_weight_coverage"]),
            "minimum_for_full_confidence": deepcopy(DEFAULT_FULL_CONFIDENCE_COVERAGE),
        },
    )
    normalized.setdefault("tab_groups", {})
    normalized.setdefault("profile_metrics", {})
    normalized.setdefault("peer_medians", {})
    ensure_v5_defaults(normalized)
    migrate_metric_ids(normalized.get("metrics", {}))
    for profile_metrics in normalized["profile_metrics"].values():
        migrate_metric_ids(profile_metrics)
    validate_config(normalized)
    return normalized


def migrate_metric_ids(metrics_by_tab: dict[str, Any]) -> None:
    if not isinstance(metrics_by_tab, dict):
        return
    for metrics in metrics_by_tab.values():
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if isinstance(metric, dict):
                metric["id"] = LEGACY_METRIC_IDS.get(metric.get("id"), metric.get("id"))
