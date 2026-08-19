from __future__ import annotations

from typing import Any

from ticker_analyzer.domain import RatingCode
from ticker_analyzer.numbers import clean_number

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
