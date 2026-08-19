Exit code: 0
Wall time: 0.3 seconds
Output:
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from ticker_analyzer.config_defaults import (
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


class ConfigValidationError(ValueError):
    pass


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
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    return normalize_config(config)


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    config = normalize_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


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


def validate_config(config: dict[str, Any]) -> None:
    required_top_level = ["tab_weights", "rating_thresholds", "overall_rating_labels", "tab_rating_labels", "metrics"]
    for key in required_top_level:
        if key not in config:
            raise ConfigValidationError(f"Missing required config section: {key}")
    if not isinstance(config["metrics"], dict) or not config["metrics"]:
        raise ConfigValidationError("metrics must be a non-empty object keyed by tab name.")
    validate_thresholds(config.get("rating_thresholds", {}), "rating_thresholds")
    validate_tab_weights(config.get("tab_weights", {}), config["metrics"])
    validate_missing_policy(config.get("missing_policy", {}))
    unknown_required_tabs = set(config.get("missing_policy", {}).get("required_tabs", [])) - set(config["metrics"])
    if unknown_required_tabs:
        raise ConfigValidationError(
            f"missing_policy.required_tabs references unknown tabs: {sorted(unknown_required_tabs)}"
        )
    validate_minimum_coverage(config.get("minimum_weight_coverage", {}), config["metrics"])
    validate_coverage_policy(config.get("coverage_policy", {}), config["metrics"])
    for tab_name, metrics in config["metrics"].items():
        if not isinstance(metrics, list) or not metrics:
            raise ConfigValidationError(f"metrics.{tab_name} must be a non-empty list.")
        ids = [metric.get("id") for metric in metrics if isinstance(metric, dict)]
        if len(ids) != len(set(ids)):
            raise ConfigValidationError(f"metrics.{tab_name} contains duplicate metric ids.")
        for index, metric in enumerate(metrics, start=1):
            validate_metric(metric, f"metrics.{tab_name}[{index}]")
    validate_profile_metrics(config.get("profile_metrics", {}))
    validate_all_groups(config)
    validate_profile_rules(config)
    validate_data_quality(config.get("data_quality", {}))
    if config.get("scoring_model") != SCORING_MODEL:
        raise ConfigValidationError(f"Unsupported scoring_model: {config.get('scoring_model')}")
    for tab_name, thresholds in config.get("tab_rating_thresholds", {}).items():
        validate_thresholds(thresholds, f"tab_rating_thresholds.{tab_name}")


def validate_tab_weights(tab_weights: dict[str, Any], metrics_by_tab: dict[str, Any]) -> None:
    if not isinstance(tab_weights, dict):
        raise ConfigValidationError("tab_weights must be an object.")
    for tab_name in metrics_by_tab:
        weight = optional_number(tab_weights.get(tab_name))
        if weight is None:
            raise ConfigValidationError(f"tab_weights.{tab_name} must be a number.")
        if weight < 0:
            raise ConfigValidationError(f"tab_weights.{tab_name} cannot be negative.")
    total = sum(optional_number(tab_weights.get(tab_name)) or 0 for tab_name in metrics_by_tab)
    if abs(total - 1.0) > 0.001:
        raise ConfigValidationError("tab_weights must sum to 1.0 (within 0.001).")


def validate_profile_metrics(profile_metrics: dict[str, Any]) -> None:
    if not isinstance(profile_metrics, dict):
        raise ConfigValidationError("profile_metrics must be an object.")
    for profile_name, metrics_by_tab in profile_metrics.items():
        if not isinstance(metrics_by_tab, dict) or not metrics_by_tab:
            raise ConfigValidationError(f"profile_metrics.{profile_name} must be a non-empty object keyed by tab name.")
        for tab_name, metrics in metrics_by_tab.items():
            if not isinstance(metrics, list) or not metrics:
                raise ConfigValidationError(f"profile_metrics.{profile_name}.{tab_name} must be a non-empty list.")
            ids = [metric.get("id") for metric in metrics if isinstance(metric, dict)]
            if len(ids) != len(set(ids)):
                raise ConfigValidationError(f"profile_metrics.{profile_name}.{tab_name} contains duplicate metric ids.")
            for index, metric in enumerate(metrics, start=1):
                validate_metric(metric, f"profile_metrics.{profile_name}.{tab_name}[{index}]")


def validate_thresholds(thresholds: dict[str, Any], path: str) -> None:
    if not isinstance(thresholds, dict):
        raise ConfigValidationError(f"{path} must be an object.")
    ordered_keys = ["very_strong", "strong", "neutral", "weak"]
    values = [optional_number(thresholds.get(key)) for key in ordered_keys]
    if any(value is None for value in values):
        raise ConfigValidationError(f"{path} must define numeric very_strong, strong, neutral, and weak thresholds.")
    if not (values[0] >= values[1] >= values[2] >= values[3]):
        raise ConfigValidationError(f"{path} thresholds must be descending: very_strong >= strong >= neutral >= weak.")


def validate_metric(metric: dict[str, Any], path: str) -> None:
    if not isinstance(metric, dict):
        raise ConfigValidationError(f"{path} must be an object.")
    for key in ["id", "name", "weight", "direction", "warn", "good", "unit", "description"]:
        if key not in metric:
            raise ConfigValidationError(f"{path} is missing required field: {key}")
    if not str(metric["id"]).strip():
        raise ConfigValidationError(f"{path}.id cannot be empty.")
    if metric["direction"] not in {"higher", "lower"}:
        raise ConfigValidationError(f"{path}.direction must be higher or lower.")
    weight = optional_number(metric.get("weight"))
    if weight is None or weight < 0:
        raise ConfigValidationError(f"{path}.weight must be a non-negative number.")
    warn = optional_number(metric.get("warn"))
    good = optional_number(metric.get("good"))
    if warn is None or good is None:
        raise ConfigValidationError(f"{path}.warn and {path}.good must be numbers.")
    if warn == good:
        raise ConfigValidationError(f"{path}.warn and {path}.good cannot be equal.")
    if metric["direction"] == "higher" and good <= warn:
        raise ConfigValidationError(f"{path}.good must be greater than warn for higher direction.")
    if metric["direction"] == "lower" and good >= warn:
        raise ConfigValidationError(f"{path}.good must be lower than warn for lower direction.")


def validate_missing_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise ConfigValidationError("missing_policy must be an object.")
    require_all = policy.get("require_all_tabs_for_overall", False)
    if not isinstance(require_all, bool):
        raise ConfigValidationError("missing_policy.require_all_tabs_for_overall must be true or false.")
    minimum = optional_number(policy.get("minimum_scored_tabs", 2))
    if minimum is None or minimum < 1:
        raise ConfigValidationError("missing_policy.minimum_scored_tabs must be at least 1.")
    required_tabs = policy.get("required_tabs", [])
    if not isinstance(required_tabs, list) or any(not isinstance(name, str) for name in required_tabs):
        raise ConfigValidationError("missing_policy.required_tabs must be a list of tab names.")
    penalties = policy.get("missing_tab_penalty", {})
    if not isinstance(penalties, dict) or any(
        optional_number(value) is None or float(value) < 0 for value in penalties.values()
    ):
        raise ConfigValidationError("missing_policy.missing_tab_penalty values must be non-negative numbers.")


def validate_minimum_coverage(coverage: dict[str, Any], metrics_by_tab: dict[str, Any]) -> None:
    if not isinstance(coverage, dict):
        raise ConfigValidationError("minimum_weight_coverage must be an object.")
    for tab_name in metrics_by_tab:
        value = optional_number(coverage.get(tab_name))
        if value is None or not 0 <= value <= 1:
            raise ConfigValidationError(f"minimum_weight_coverage.{tab_name} must be between 0 and 1.")


def validate_coverage_policy(policy: dict[str, Any], metrics_by_tab: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise ConfigValidationError("coverage_policy must be an object.")
    for section in ("minimum_to_score", "minimum_for_full_confidence"):
        values = policy.get(section)
        if not isinstance(values, dict):
            raise ConfigValidationError(f"coverage_policy.{section} must be an object.")
        for tab_name in metrics_by_tab:
            value = optional_number(values.get(tab_name))
            if value is None or not 0 <= value <= 1:
                raise ConfigValidationError(f"coverage_policy.{section}.{tab_name} must be between 0 and 1.")


def validate_data_quality(data_quality: dict[str, Any]) -> None:
    if not isinstance(data_quality, dict) or not isinstance(data_quality.get("weights"), dict):
        raise ConfigValidationError("data_quality.weights must be an object.")
    defaults = default_data_quality_config()
    component_weights = data_quality.get("component_weights")
    if component_weights is None:
        component_weights = data_quality["weights"]
    if not isinstance(component_weights, dict):
        raise ConfigValidationError("data_quality.component_weights must be an object.")
    required = set(defaults["component_weights"])
    if set(component_weights) != required:
        raise ConfigValidationError(f"data_quality.component_weights must define exactly: {sorted(required)}")
    parsed = [optional_number(component_weights[name]) for name in required]
    if any(value is None or value < 0 for value in parsed):
        raise ConfigValidationError("data_quality component weights must be non-negative numbers.")
    if abs(sum(value or 0 for value in parsed) - 1.0) > 0.001:
        raise ConfigValidationError("data_quality component weights must sum to 1.0.")

    # Streamlit can briefly keep the old module while pulling the new config.
    # Retaining the v5 weight schema makes that rolling state load safely.
    legacy_weights = data_quality["weights"]
    legacy_required = set(defaults["weights"])
    if set(legacy_weights) != legacy_required:
        raise ConfigValidationError(f"data_quality.weights must define exactly: {sorted(legacy_required)}")
    legacy_parsed = [optional_number(legacy_weights[name]) for name in legacy_required]
    if any(value is None or value < 0 for value in legacy_parsed):
        raise ConfigValidationError("data_quality legacy weights must be non-negative numbers.")
    if abs(sum(value or 0 for value in legacy_parsed) - 1.0) > 0.001:
        raise ConfigValidationError("data_quality legacy weights must sum to 1.0.")


def validate_all_groups(config: dict[str, Any]) -> None:
    validate_groups_for_tabs(config.get("tab_groups", {}), config.get("metrics", {}), "tab_groups")
    for profile, metrics_by_tab in config.get("profile_metrics", {}).items():
        groups = config.get("profile_tab_groups", {}).get(profile)
        if groups is None:
            continue
        validate_groups_for_tabs(groups, metrics_by_tab, f"profile_tab_groups.{profile}")


def validate_groups_for_tabs(groups_by_tab: dict[str, Any], metrics_by_tab: dict[str, Any], path: str) -> None:
    if not isinstance(groups_by_tab, dict):
        raise ConfigValidationError(f"{path} must be an object.")
    for tab_name, groups in groups_by_tab.items():
        metrics = metrics_by_tab.get(tab_name, [])
        metric_ids = {metric.get("id") for metric in metrics}
        positive_ids = {
            metric.get("id") for metric in metrics if (optional_number(metric.get("weight")) or 0) > 0
        }
        validate_groups(groups, metric_ids, f"{path}.{tab_name}", positive_ids=positive_ids)


def validate_groups(
    groups: dict[str, Any], metric_ids: set[str], path: str, *, positive_ids: set[str] | None = None
) -> None:
    if not isinstance(groups, dict) or not groups:
        raise ConfigValidationError(f"{path} must be a non-empty object.")
    total_weight = 0.0
    memberships: dict[str, list[str]] = {}
    for group_name, definition in groups.items():
        if not isinstance(definition, dict):
            raise ConfigValidationError(f"{path}.{group_name} must be an object.")
        weight = optional_number(definition.get("weight"))
        if weight is None or weight < 0:
            raise ConfigValidationError(f"{path}.{group_name}.weight is invalid.")
        total_weight += weight
        members = definition.get("metrics", [])
        if not isinstance(members, list) or not members:
            raise ConfigValidationError(f"{path}.{group_name}.metrics must be a non-empty list.")
        for metric_id in members:
            if metric_id not in metric_ids:
                raise ConfigValidationError(f"{path}.{group_name} references unknown metric: {metric_id}")
            memberships.setdefault(metric_id, []).append(group_name)
    if abs(total_weight - 1.0) > 0.001:
        raise ConfigValidationError(f"{path} group weights must sum to 1.0.")
    duplicates = {metric_id: names for metric_id, names in memberships.items() if len(names) > 1}
    if duplicates:
        raise ConfigValidationError(f"{path} contains duplicate group membership: {duplicates}")
    missing = (positive_ids or set()) - set(memberships)
    if missing:
        raise ConfigValidationError(f"{path} omits positive-weight metrics: {sorted(missing)}")


def validate_profile_rules(config: dict[str, Any]) -> None:
    rules = config.get("profile_rules", {})
    if not isinstance(rules, dict):
        raise ConfigValidationError("profile_rules must be an object.")
    for profile, tabs in rules.items():
        if not isinstance(tabs, dict):
            raise ConfigValidationError(f"profile_rules.{profile} must be an object.")
        groups_by_tab = config.get("profile_tab_groups", {}).get(profile, config.get("tab_groups", {}))
        for tab_name, rule in tabs.items():
            coverage = optional_number(rule.get("minimum_coverage"))
            if coverage is None or not 0 <= coverage <= 1:
                raise ConfigValidationError(f"profile_rules.{profile}.{tab_name}.minimum_coverage must be 0..1.")
            for group_name, requirement in rule.get("required_groups", {}).items():
                if group_name not in groups_by_tab.get(tab_name, {}):
                    raise ConfigValidationError(
                        f"profile_rules.{profile}.{tab_name} references unknown group: {group_name}"
                    )
                minimum = optional_number(requirement.get("minimum_available_metrics", 1))
                if minimum is None or minimum < 0:
                    raise ConfigValidationError(
                        f"profile_rules.{profile}.{tab_name}.{group_name} minimum is invalid."
                    )


def optional_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result
