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
    return calculate_rating_decision(overall, data_quality, tabs, config)["rating_code"]


def calculate_rating_decision(
    overall: float | None,
    data_quality: float,
    tabs: dict[str, float | None],
    config: dict[str, Any],
    *,
    model_applicability: float = 100.0,
    profile_rating_cap: RatingCode | None = None,
) -> dict[str, Any]:
    """Return the v5.1 rating plus every gate/cap used to reach it."""
    gates = config.get("rating_gates", {})
    reasons: list[str] = []
    active_caps: list[str] = []
    fundamentals = clean_number(tabs.get("Fundamentals"))
    available_tabs = [clean_number(value) for value in tabs.values()]
    available_tabs = [value for value in available_tabs if value is not None]
    if overall is None or fundamentals is None or len(available_tabs) < 2:
        return _rating_decision("insufficient_data", "None", active_caps, ["insufficient_scored_tabs"])
    if data_quality < number_or_default(gates.get("minimum_data_quality_for_rating"), 40):
        return _rating_decision("insufficient_data", "None", active_caps, ["data_quality_below_40"])

    minimum_tab = min(available_tabs)
    code = classify_rating_code(overall, config)
    reasons.append(f"base_rating_{code}")
    if len(available_tabs) < len(tabs):
        reasons.append(f"partial_overall_{len(tabs) - len(available_tabs)}_missing_tab")
    if minimum_tab < 30:
        reasons.append("weakest_tab_penalty_4")
    elif minimum_tab < 40:
        reasons.append("weakest_tab_penalty_2")
    very_strong = gates.get("very_strong", {})
    if code == "very_strong" and not (
        overall >= number_or_default(very_strong.get("minimum_overall_score"), 80)
        and data_quality >= number_or_default(very_strong.get("minimum_data_quality"), 65)
        and minimum_tab >= number_or_default(very_strong.get("minimum_each_tab"), 40)
        and fundamentals >= number_or_default(very_strong.get("minimum_fundamentals"), 50)
    ):
        code = "strong"
        reasons.append("strong_buy_gate_failed")
    strong = gates.get("strong", {})
    if code == "strong" and not (
        overall >= number_or_default(strong.get("minimum_overall_score"), 67)
        and data_quality >= number_or_default(strong.get("minimum_data_quality"), 55)
        and minimum_tab >= number_or_default(strong.get("minimum_each_tab"), 30)
        and fundamentals >= number_or_default(strong.get("minimum_fundamentals"), 45)
    ):
        code = "neutral"
        reasons.append("buy_gate_failed")

    confidence = "High" if data_quality >= 65 else "Medium" if data_quality >= 55 else "Low"
    if data_quality < 55:
        code = _apply_cap(code, "neutral", "data_quality_cap_hold", active_caps, reasons)
    elif data_quality < 65:
        code = _apply_cap(code, "strong", "data_quality_cap_buy", active_caps, reasons)
    if model_applicability < 40:
        code = _apply_cap(code, "neutral", "model_applicability_cap_hold", active_caps, reasons)
    elif model_applicability < 65:
        code = _apply_cap(code, "strong", "model_applicability_cap_buy", active_caps, reasons)
    if profile_rating_cap is not None:
        code = _apply_cap(code, profile_rating_cap, "profile_model_cap", active_caps, reasons)
    return _rating_decision(code, confidence, active_caps, reasons)


def _apply_cap(
    code: RatingCode,
    maximum: RatingCode,
    reason: str,
    active_caps: list[str],
    reasons: list[str],
) -> RatingCode:
    if reason not in active_caps:
        active_caps.append(reason)
    capped = cap_rating_code(code, maximum)
    if capped != code:
        reasons.append(reason)
    return capped


def _rating_decision(
    code: RatingCode, confidence: str, active_caps: list[str], reasons: list[str]
) -> dict[str, Any]:
    return {
        "rating_code": code,
        "rating_confidence": confidence,
        "rating_caps": active_caps,
        "rating_reason_codes": reasons,
    }


def classify_rating_code(score: float, config: dict[str, Any]) -> RatingCode:
    thresholds = config.get("rating_thresholds", {})
    if score >= number_or_default(thresholds.get("very_strong"), 80):
        return "very_strong"
    if score >= number_or_default(thresholds.get("strong"), 67):
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
