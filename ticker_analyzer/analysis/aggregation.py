from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.analysis.quality import confidence_label
from ticker_analyzer.metrics.utils import clean_number
from ticker_analyzer.scoring import ScoringEngine


def years_from_range(price_range: str) -> int:
    normalized = price_range.strip().lower()
    if normalized.endswith("y"):
        try:
            return max(1, int(normalized[:-1]))
        except ValueError:
            return 2
    return 2


def is_empty_ticker_response(
    info: dict[str, Any],
    annual_income: pd.DataFrame,
    annual_balance: pd.DataFrame,
    annual_cashflow: pd.DataFrame,
    history: pd.DataFrame,
) -> bool:
    has_identity = bool(info.get("longName") or info.get("shortName") or info.get("symbol"))
    has_prices = not history.empty and "Close" in history and not history["Close"].dropna().empty
    has_financials = not annual_income.empty or not annual_balance.empty or not annual_cashflow.empty
    return not has_identity and not has_prices and not has_financials


def overall_score_with_missing_policy(tab_results: dict[str, Any], config: dict[str, Any]) -> float | None:
    tab_weights = config.get("tab_weights", {})
    policy = config.get("missing_policy", {})
    scored_tabs = [result for result in tab_results.values() if result.get("score") is not None]
    if policy.get("require_all_tabs_for_overall", False) and len(scored_tabs) < len(tab_results):
        return None
    required_tabs = policy.get("required_tabs", [])
    if any(tab_results.get(name, {}).get("score") is None for name in required_tabs):
        return None
    minimum_scored_tabs = int(clean_number(policy.get("minimum_scored_tabs")) or 1)
    if len(scored_tabs) < minimum_scored_tabs:
        return None
    weighted_mean = weighted_tab_score_from_results(tab_results, tab_weights)
    if weighted_mean is None:
        return None
    available_scores = [float(result["score"]) for result in tab_results.values() if result.get("score") is not None]
    missing_count = len(tab_results) - len(available_scores)
    missing_penalties = policy.get("missing_tab_penalty", {"0": 0, "1": 5, "2": 15})
    missing_penalty = float(missing_penalties.get(str(missing_count), missing_penalties.get(missing_count, 0)))
    weakest = min(available_scores)
    weakest_penalty = 4.0 if weakest < 30 else 2.0 if weakest < 40 else 0.0
    return max(0.0, weighted_mean - missing_penalty - weakest_penalty)


def weighted_tab_score_from_results(tab_results: dict[str, Any], tab_weights: dict[str, Any]) -> float | None:
    scoring = ScoringEngine()
    return scoring.weighted_tab_score(tab_results, tab_weights)


def partial_overall_note(tab_results: dict[str, Any], overall_score: float | None) -> str | None:
    if overall_score is None:
        return None
    scored = sum(1 for result in tab_results.values() if result.get("score") is not None)
    total = len(tab_results)
    if scored == total:
        return None
    return f"Overall: Partial rating based on {scored} of {total} scored tabs."


def metric_coverage(
    metrics: list[Any], tab_name: str | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    positive_group_metric_ids: set[str] | None = None
    if tab_name and config:
        groups = config.get("tab_groups", {}).get(tab_name, {})
        if groups:
            positive_group_metric_ids = {
                metric_id
                for definition in groups.values()
                if float(definition.get("weight", 0)) > 0
                for metric_id in definition.get("metrics", [])
            }
    eligible = [
        metric for metric in metrics
        if metric.weight > 0
        and (positive_group_metric_ids is None or metric.id in positive_group_metric_ids)
    ]
    scored = [metric for metric in eligible if metric.score is not None]
    scored_weight = sum(metric.weight for metric in scored)
    total_weight = sum(metric.weight for metric in eligible)
    percentage = scored_weight / total_weight * 100 if total_weight > 0 else 0.0
    return {
        "scored_metrics": len(scored),
        "total_metrics": len(eligible),
        "scored_weight": scored_weight,
        "total_weight": total_weight,
        "percentage": percentage,
        "confidence": confidence_label(percentage),
    }


def grouped_tab_score(
    tab_name: str,
    metrics: list[Any],
    config: dict[str, Any],
    coverage: dict[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    active_rule = config.get("active_profile_rules", {}).get(tab_name, {})
    minimum = float(
        active_rule.get("minimum_coverage", config.get("minimum_weight_coverage", {}).get(tab_name, 0))
    ) * 100
    by_id = {metric.id: metric for metric in metrics}
    available_ids = {metric.id for metric in metrics if metric.score is not None and metric.weight > 0}
    groups = config.get("tab_groups", {}).get(tab_name, {})
    breakdown: dict[str, Any] = {"minimum_coverage": minimum, "groups": {}, "caps": []}
    if coverage["percentage"] + 1e-9 < minimum:
        breakdown["reason"] = "minimum_weight_coverage"
        return None, breakdown

    if not groups:
        return ScoringEngine().weighted_score(metrics), breakdown
    configured_ids = set(by_id)
    if not any(configured_ids & set(definition.get("metrics", [])) for definition in groups.values()):
        return ScoringEngine().weighted_score(metrics), breakdown
    scored_groups: list[tuple[float, float]] = []
    for group_name, definition in groups.items():
        members = [by_id[item] for item in definition.get("metrics", []) if item in by_id]
        group_score = ScoringEngine().weighted_score(members)
        group_weight = float(definition.get("weight", 0))
        member_ids = definition.get("metrics", [])
        breakdown["groups"][group_name] = {
            "score": group_score,
            "weight": group_weight,
            "available_metrics": len(available_ids & set(member_ids)),
            "total_metrics": len(member_ids),
        }
        if group_score is not None and group_weight > 0:
            scored_groups.append((group_score, group_weight))
    for group_name, requirement in active_rule.get("required_groups", {}).items():
        minimum_available = int(requirement.get("minimum_available_metrics", 1))
        available = breakdown["groups"].get(group_name, {}).get("available_metrics", 0)
        if available < minimum_available:
            breakdown["reason"] = "required_group_missing"
            breakdown["failed_group"] = group_name
            return None, breakdown

    total_group_weight = sum(weight for _, weight in scored_groups)
    if total_group_weight <= 0:
        return None, breakdown
    return sum(score * weight for score, weight in scored_groups) / total_group_weight, breakdown


def profile_cap_applies(config: dict[str, Any], tab_name: str) -> bool:
    return config.get("active_profile") == "Financial" and tab_name == "Fundamentals"
