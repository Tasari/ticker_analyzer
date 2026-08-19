from __future__ import annotations

import math
from typing import Any

from ticker_analyzer.domain import MetricResult
from ticker_analyzer.numbers import clean_number
from ticker_analyzer.ratings import (
    RATING_CODE_RANK,
    RATING_RANK,
    calculate_overall_rating,
    calculate_overall_rating_code,
    calculate_rating_decision,
    cap_rating,
    cap_rating_code,
    classify_five_point_score,
    classify_rating,
    classify_rating_code,
    classify_tab_rating,
    number_or_default,
    rating_label,
    tab_labels,
    tab_thresholds,
)

__all__ = [
    "ScoringEngine", "format_metric_value", "clean_number", "metric_description",
    "format_threshold", "score_higher", "score_lower", "score_value",
    "percentile_score", "apply_absolute_guardrail", "status_from_score",
    "weighted_score", "weighted_tab_score", "classify_rating",
    "calculate_overall_rating", "calculate_overall_rating_code",
    "calculate_rating_decision", "classify_rating_code", "rating_label",
    "cap_rating_code", "cap_rating", "classify_five_point_score",
    "number_or_default", "classify_tab_rating", "tab_thresholds", "tab_labels",
    "RATING_RANK", "RATING_CODE_RANK",
]


def format_metric_value(value: float | None, unit: str) -> str:
    if value is None:
        return "Missing"
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "$B":
        return f"${value:.2f}B"
    if unit == "pp":
        return f"{value:.2f} pp"
    return f"{value:.2f}"


class ScoringEngine:
    def score_metric(
        self,
        metric_config: dict[str, Any],
        raw_metrics: dict[str, dict[str, Any]],
        tab_name: str,
        config: dict[str, Any],
    ) -> MetricResult:
        metric_id = metric_config["id"]
        raw = raw_metrics.get(metric_id, {})
        value = clean_number(raw.get("value"))
        if value is None:
            return MetricResult(
                id=metric_id,
                name=metric_config.get("name", metric_id),
                value=None,
                unit=metric_config.get("unit", ""),
                score=None,
                weight=clean_number(metric_config.get("weight")) or 0,
                status="Missing",
                note=raw.get("note", "data unavailable"),
                description=metric_description(metric_config),
                provenance=raw.get("provenance"),
            )
        scoring_config = metric_config
        if metric_config.get("scoring") == "percentile":
            scoring_config = {**metric_config, "percentile": raw.get("percentile")}
        score = apply_absolute_guardrail(
            metric_id,
            value,
            score_value(value, scoring_config),
            config,
        )
        return MetricResult(
            id=metric_id,
            name=metric_config.get("name", metric_id),
            value=value,
            unit=metric_config.get("unit", ""),
            score=score,
            weight=clean_number(metric_config.get("weight")) or 0,
            status=status_from_score(score, tab_name, config),
            note=raw.get("note", ""),
            description=metric_description(metric_config),
            provenance=raw.get("provenance"),
        )

    def weighted_score(self, metrics: list[MetricResult]) -> float | None:
        return weighted_score(metrics)

    def weighted_tab_score(self, tab_results: dict[str, Any], tab_weights: dict[str, Any]) -> float | None:
        return weighted_tab_score(tab_results, tab_weights)

    def classify_rating(self, score: float | None, config: dict[str, Any]) -> str:
        return classify_rating(score, config)

    def classify_tab_rating(self, tab_name: str, score: float | None, config: dict[str, Any]) -> str:
        return classify_tab_rating(tab_name, score, config)


def metric_description(metric_config: dict[str, Any]) -> str:
    description = metric_config.get("description", "")
    direction = metric_config.get("direction", "higher")
    good = metric_config.get("good")
    warn = metric_config.get("warn")
    unit = metric_config.get("unit", "")
    weight = clean_number(metric_config.get("weight")) or 0
    direction_text = "higher values improve the score" if direction == "higher" else "lower values improve the score"
    scoring = (
        f"Scoring: {direction_text}. Good threshold: {format_threshold(good, unit)}. "
        f"Weak threshold: {format_threshold(warn, unit)}. Weight: {weight:g}."
    )
    return f"{description} {scoring}".strip()


def format_threshold(value: Any, unit: str) -> str:
    number = clean_number(value)
    if number is None:
        return "not set"
    if unit == "%":
        return f"{number:g}%"
    if unit == "x":
        return f"{number:g}x"
    if unit == "$B":
        return f"${number:g}B"
    if unit == "pp":
        return f"{number:g} pp"
    return f"{number:g}"


def score_higher(value: float, warn: float, good: float) -> float:
    if not all(math.isfinite(item) for item in (value, warn, good)):
        raise ValueError("value, warn and good must be finite")
    if good <= warn:
        raise ValueError("good must be greater than warn for direction=higher")
    span = good - warn
    floor_value, ceiling_value = warn - span, good + span
    if value <= floor_value:
        return 0.0
    if value < warn:
        return 25.0 * (value - floor_value) / span
    if value < good:
        return 25.0 + 50.0 * (value - warn) / span
    if value < ceiling_value:
        return 75.0 + 25.0 * (value - good) / span
    return 100.0


def score_lower(value: float, warn: float, good: float) -> float:
    if not all(math.isfinite(item) for item in (value, warn, good)):
        raise ValueError("value, warn and good must be finite")
    if good >= warn:
        raise ValueError("good must be lower than warn for direction=lower")
    span = warn - good
    floor_value, ceiling_value = good - span, warn + span
    if value >= ceiling_value:
        return 0.0
    if value > warn:
        return 25.0 * (ceiling_value - value) / span
    if value > good:
        return 25.0 + 50.0 * (warn - value) / span
    if value > floor_value:
        return 75.0 + 25.0 * (good - value) / span
    return 100.0


def score_value(value: float, metric_config: dict[str, Any]) -> float:
    if metric_config.get("scoring") == "percentile":
        percentile = clean_number(metric_config.get("percentile"))
        if percentile is None:
            raise ValueError("percentile scoring requires a finite percentile")
        return percentile_score(percentile)
    good = clean_number(metric_config.get("good"))
    warn = clean_number(metric_config.get("warn"))
    direction = metric_config.get("direction", "higher")
    if good is None or warn is None:
        raise ValueError("metric thresholds must be finite")
    if direction == "lower":
        return score_lower(value, warn, good)
    if direction == "higher":
        return score_higher(value, warn, good)
    raise ValueError(f"unsupported metric direction: {direction}")


def percentile_score(percentile: float) -> float:
    """Map a 0..1 peer percentile to the v5.1 score anchors."""
    if not math.isfinite(percentile):
        raise ValueError("percentile must be finite")
    value = max(0.0, min(float(percentile), 1.0))
    anchors = [
        (0.00, 0.0),
        (0.10, 15.0),
        (0.25, 30.0),
        (0.50, 50.0),
        (0.75, 70.0),
        (0.90, 85.0),
        (0.97, 95.0),
        (1.00, 100.0),
    ]
    for (left_x, left_y), (right_x, right_y) in zip(anchors, anchors[1:], strict=False):
        if value <= right_x:
            fraction = (value - left_x) / (right_x - left_x)
            return left_y + fraction * (right_y - left_y)
    return 100.0


def apply_absolute_guardrail(
    metric_id: str, value: float, score: float, config: dict[str, Any]
) -> float:
    """Cap peer/absolute scores when a raw metric reveals economic distress."""
    rules = config.get("absolute_guardrails", {}).get(metric_id, [])
    for rule in rules:
        boundary = clean_number(rule.get("at_or_below"))
        maximum = clean_number(rule.get("maximum_score"))
        if boundary is not None and maximum is not None and value <= boundary:
            score = min(score, maximum)
    return score


def status_from_score(score: float, tab_name: str, config: dict[str, Any]) -> str:
    labels = config.get("tab_rating_labels", {}).get(tab_name, {})
    thresholds = tab_thresholds(tab_name, config)
    return classify_five_point_score(score, thresholds, tab_labels(labels))


def weighted_score(metrics: list[MetricResult]) -> float | None:
    available = [metric for metric in metrics if metric.score is not None and metric.weight > 0]
    total_weight = sum(metric.weight for metric in available)
    if total_weight <= 0:
        return None
    return sum((metric.score or 0) * metric.weight for metric in available) / total_weight


def weighted_tab_score(tab_results: dict[str, Any], tab_weights: dict[str, Any]) -> float | None:
    weighted: list[tuple[float, float]] = []
    for tab_name, result in tab_results.items():
        score = clean_number(result.get("score"))
        weight = clean_number(tab_weights.get(tab_name)) or 0
        if score is not None and weight > 0:
            weighted.append((score, weight))
    total_weight = sum(weight for _, weight in weighted)
    if total_weight <= 0:
        return None
    return sum(score * weight for score, weight in weighted) / total_weight
