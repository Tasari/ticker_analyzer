from __future__ import annotations

import math
from typing import Any

from ticker_analyzer.domain import MetricResult, RatingCode

RATING_RANK = {
    "Strong Sell": 0,
    "Sell": 1,
    "Hold": 2,
    "Buy": 3,
    "Strong Buy": 4,
}

RATING_CODE_RANK: dict[RatingCode, int] = {
    "very_weak": 0,
    "weak": 1,
    "neutral": 2,
    "strong": 3,
    "very_strong": 4,
    "not_rated": -1,
    "insufficient_data": -1,
}


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
        score = score_value(value, metric_config)
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


def clean_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


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


def classify_rating(score: float | None, config: dict[str, Any]) -> str:
    if score is None:
        return "Not Rated"
    default_labels = {
        "very_strong": "Strong Buy",
        "strong": "Buy",
        "neutral": "Hold",
        "weak": "Sell",
        "very_weak": "Strong Sell",
    }
    labels = {**default_labels, **config.get("overall_rating_labels", {})}
    return classify_five_point_score(score, config.get("rating_thresholds", {}), labels)


def calculate_overall_rating(
    overall: float | None,
    confidence: float,
    tabs: dict[str, float | None],
    config: dict[str, Any],
) -> str:
    code = calculate_overall_rating_code(overall, confidence, tabs, config)
    return rating_label(code, config)


def calculate_overall_rating_code(
    overall: float | None,
    data_quality: float,
    tabs: dict[str, float | None],
    config: dict[str, Any],
) -> RatingCode:
    gates = config.get("rating_gates", {})
    minimum_quality = number_or_default(gates.get("minimum_data_quality_for_directional_rating"), 60)
    if data_quality < minimum_quality:
        return "not_rated"
    if overall is None or any(tabs.get(name) is None for name in ("Growth", "Fundamentals", "Value")):
        return "insufficient_data"
    growth = float(tabs["Growth"])
    fundamentals = float(tabs["Fundamentals"])
    value = float(tabs["Value"])
    minimum_tab = min(growth, fundamentals, value)
    code = classify_rating_code(overall, config)
    very_strong = gates.get("very_strong", {})
    if code == "very_strong" and not (
        overall >= number_or_default(very_strong.get("minimum_overall_score"), 85)
        and data_quality >= number_or_default(very_strong.get("minimum_data_quality"), 80)
        and minimum_tab >= number_or_default(very_strong.get("minimum_each_tab"), 50)
        and fundamentals >= number_or_default(very_strong.get("minimum_fundamentals"), 55)
        and value >= number_or_default(very_strong.get("minimum_value"), 45)
    ):
        code = "strong"
    strong = gates.get("strong", {})
    if code == "strong" and not (
        overall >= number_or_default(strong.get("minimum_overall_score"), 72)
        and data_quality >= number_or_default(strong.get("minimum_data_quality"), 70)
        and minimum_tab >= number_or_default(strong.get("minimum_each_tab"), 40)
        and fundamentals >= number_or_default(strong.get("minimum_fundamentals"), 50)
    ):
        code = "neutral"
    if fundamentals < 30:
        code = cap_rating_code(code, "neutral")
    if value < 20:
        code = cap_rating_code(code, "neutral")
    return code


def classify_rating_code(score: float, config: dict[str, Any]) -> RatingCode:
    thresholds = config.get("rating_thresholds", {})
    if score >= number_or_default(thresholds.get("very_strong"), 85):
        return "very_strong"
    if score >= number_or_default(thresholds.get("strong"), 70):
        return "strong"
    if score >= number_or_default(thresholds.get("neutral"), 45):
        return "neutral"
    if score >= number_or_default(thresholds.get("weak"), 30):
        return "weak"
    return "very_weak"


def rating_label(code: RatingCode, config: dict[str, Any]) -> str:
    defaults = {
        "very_strong": "Strong Buy",
        "strong": "Buy",
        "neutral": "Hold",
        "weak": "Sell",
        "very_weak": "Strong Sell",
        "not_rated": "Not Rated – Low Data Quality",
        "insufficient_data": "Insufficient Data",
    }
    return str({**defaults, **config.get("overall_rating_labels", {})}.get(code, defaults[code]))


def cap_rating_code(base: RatingCode, maximum: RatingCode) -> RatingCode:
    return base if RATING_CODE_RANK[base] <= RATING_CODE_RANK[maximum] else maximum


def cap_rating(base_rating: str, maximum_rating: str) -> str:
    if RATING_RANK[base_rating] <= RATING_RANK[maximum_rating]:
        return base_rating
    return maximum_rating


def classify_five_point_score(
    score: float,
    thresholds: dict[str, Any],
    labels: dict[str, str],
) -> str:
    very_strong = number_or_default(thresholds.get("very_strong"), 85)
    strong = number_or_default(thresholds.get("strong"), 70)
    neutral = number_or_default(thresholds.get("neutral"), 45)
    weak = number_or_default(thresholds.get("weak"), 30)
    if score >= very_strong:
        return labels["very_strong"]
    if score >= strong:
        return labels["strong"]
    if score >= neutral:
        return labels["neutral"]
    if score >= weak:
        return labels["weak"]
    return labels["very_weak"]


def number_or_default(value: Any, default: float) -> float:
    parsed = clean_number(value)
    return default if parsed is None else parsed


def classify_tab_rating(tab_name: str, score: float | None, config: dict[str, Any]) -> str:
    if score is None:
        return "Not Rated"
    labels = config.get("tab_rating_labels", {}).get(tab_name, {})
    thresholds = tab_thresholds(tab_name, config)
    return classify_five_point_score(score, thresholds, tab_labels(labels))


def tab_thresholds(tab_name: str, config: dict[str, Any]) -> dict[str, Any]:
    default_thresholds = config.get("rating_thresholds", {})
    per_tab = config.get("tab_rating_thresholds", {}).get(tab_name, {})
    return {**default_thresholds, **per_tab}


def tab_labels(labels: dict[str, str]) -> dict[str, str]:
    defaults = {
        "very_strong": "Very Good",
        "strong": "Good",
        "neutral": "Watch",
        "weak": "Weak",
        "very_weak": "Very Weak",
    }
    return {**defaults, **labels}
