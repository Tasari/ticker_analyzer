from __future__ import annotations

from typing import Any

from ticker_analyzer.domain import MetricResult


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


def score_value(value: float, metric_config: dict[str, Any]) -> float:
    good = clean_number(metric_config.get("good"))
    warn = clean_number(metric_config.get("warn"))
    direction = metric_config.get("direction", "higher")
    if good is None or warn is None or good == warn:
        return 50
    if direction == "lower":
        if value <= good:
            return 100
        if value >= warn:
            return 0
        return (warn - value) / (warn - good) * 100
    if value >= good:
        return 100
    if value <= warn:
        return 0
    return (value - warn) / (good - warn) * 100


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


def classify_five_point_score(
    score: float,
    thresholds: dict[str, Any],
    labels: dict[str, str],
) -> str:
    very_strong = clean_number(thresholds.get("very_strong")) or 80
    strong = clean_number(thresholds.get("strong")) or 60
    neutral = clean_number(thresholds.get("neutral")) or 40
    weak = clean_number(thresholds.get("weak")) or 20
    if score >= very_strong:
        return labels["very_strong"]
    if score >= strong:
        return labels["strong"]
    if score >= neutral:
        return labels["neutral"]
    if score >= weak:
        return labels["weak"]
    return labels["very_weak"]


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
