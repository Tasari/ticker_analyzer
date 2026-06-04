from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONFIG_PATH = Path("metrics_config.json")
CONFIG_VERSION = 1


class ConfigValidationError(ValueError):
    pass


class MetricsConfig:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "MetricsConfig":
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
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigValidationError("Configuration must be a JSON object.")
    normalized = dict(config)
    normalized.setdefault("version", CONFIG_VERSION)
    normalized.setdefault("tab_weights", {})
    normalized.setdefault("rating_thresholds", {})
    normalized.setdefault("overall_rating_labels", {})
    normalized.setdefault("tab_rating_labels", {})
    normalized.setdefault("tab_rating_thresholds", {})
    validate_config(normalized)
    return normalized


def validate_config(config: dict[str, Any]) -> None:
    required_top_level = ["tab_weights", "rating_thresholds", "overall_rating_labels", "tab_rating_labels", "metrics"]
    for key in required_top_level:
        if key not in config:
            raise ConfigValidationError(f"Missing required config section: {key}")
    if not isinstance(config["metrics"], dict) or not config["metrics"]:
        raise ConfigValidationError("metrics must be a non-empty object keyed by tab name.")
    validate_thresholds(config.get("rating_thresholds", {}), "rating_thresholds")
    validate_tab_weights(config.get("tab_weights", {}), config["metrics"])
    for tab_name, metrics in config["metrics"].items():
        if not isinstance(metrics, list) or not metrics:
            raise ConfigValidationError(f"metrics.{tab_name} must be a non-empty list.")
        for index, metric in enumerate(metrics, start=1):
            validate_metric(metric, f"metrics.{tab_name}[{index}]")
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
    for key in ["id", "name", "weight", "direction", "warn", "good", "unit"]:
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


def optional_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result
