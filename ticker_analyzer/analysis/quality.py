from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.data_quality import calculate_data_quality, freshness_score
from ticker_analyzer.domain import MarketData
from ticker_analyzer.metrics.utils import clean_number


def analysis_data_quality(
    tab_results: dict[str, Any],
    config: dict[str, Any],
    profile: str,
    data: MarketData,
) -> tuple[float, dict[str, Any]]:
    weight_coverage = analysis_coverage(tab_results, config)["percentage"]
    financial_provenance = data.provenance.get("financials")
    freshness_date = (
        financial_provenance.filed_at or financial_provenance.period_end
        if financial_provenance else None
    )
    freshness = freshness_score(pd.Timestamp(freshness_date) if freshness_date else None)
    quarterly_observations = max(
        (len(frame.columns) for frame in (data.quarterly_income, data.quarterly_balance, data.quarterly_cashflow)),
        default=0,
    )
    annual_observations = max(
        (len(frame.columns) for frame in (data.annual_income, data.annual_balance, data.annual_cashflow)),
        default=0,
    )
    actual_observations = quarterly_observations or annual_observations * 4
    provenance_items = list(data.provenance.values())
    provenance_score = (
        sum(100 if item.is_primary_source else 35 if item.fallback_level == "estimated" else 75 for item in provenance_items)
        / len(provenance_items)
        if provenance_items else 0
    )
    has_primary = any(item.is_primary_source for item in provenance_items)
    has_secondary = any(not item.is_primary_source for item in provenance_items)
    source_mix = (
        "primary_and_secondary" if has_primary and has_secondary
        else "primary_only" if has_primary
        else "secondary_only"
    )
    reconciliation_items = [
        item
        for frame in (
            data.annual_income,
            data.annual_balance,
            data.annual_cashflow,
            data.quarterly_income,
            data.quarterly_balance,
            data.quarterly_cashflow,
        )
        for item in frame.attrs.get("reconciliation", [])
    ]
    relative_differences = [float(item.get("relative_difference", 0)) for item in reconciliation_items]
    reconciliation_score = (
        max(
            0.0,
            100.0
            - sum(min(value, 1.0) for value in relative_differences) / len(relative_differences) * 100,
        )
        if relative_differences
        else None
    )
    score, breakdown = calculate_data_quality(
        metric_weight_coverage=weight_coverage,
        filing_freshness=freshness,
        provenance_score=provenance_score,
        source_mix=source_mix,
        has_period_mismatch=has_statement_period_mismatch(data),
        reconciliation_score=reconciliation_score,
        has_critical_mismatch=any(value > 0.20 for value in relative_differences),
        config=config,
    )
    breakdown.update(
        {
            "actual_observations": actual_observations,
            "reconciled_observations": len(relative_differences),
        }
    )
    return score, breakdown


def analysis_model_applicability(
    config: dict[str, Any], profile: str, data: MarketData
) -> tuple[float, list[str]]:
    """Score how well the active analytical model fits the company, not its data."""
    settings = config.get("model_applicability", {})
    score = float(settings.get("native", 90))
    warnings: list[str] = []
    if config.get("active_metric_model") == "generic_financial_fallback":
        score = min(score, float(settings.get("generic_financial_maximum", 65)))
        warnings.append("Rating limited to Buy by generic financial model")
    override_without_evidence = (
        data.ticker.upper() in config.get("profile_overrides", {})
        and not any(data.official_ids.get(key) for key in ("fdic_cert", "finra_crd", "naic_code"))
    )
    if override_without_evidence:
        score = min(score, float(settings.get("manual_override_without_evidence", 60)))
        warnings.append("Profile override lacks a matching regulatory identifier")
    return max(0.0, min(score, 100.0)), warnings


# Compatibility for callers which imported the v3 helper.
calculate_confidence = analysis_data_quality


def analysis_coverage(tab_results: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    tab_weights = config.get("tab_weights", {})
    weighted_coverage = 0.0
    total_tab_weight = 0.0
    scored_tabs = 0
    for tab_name, result in tab_results.items():
        if result.get("score") is not None:
            scored_tabs += 1
        weight = clean_number(tab_weights.get(tab_name)) or 0
        if weight <= 0:
            continue
        percentage = clean_number(result.get("coverage", {}).get("percentage")) or 0
        weighted_coverage += percentage * weight
        total_tab_weight += weight
    percentage = weighted_coverage / total_tab_weight if total_tab_weight > 0 else 0.0
    return {
        "scored_tabs": scored_tabs,
        "total_tabs": len(tab_results),
        "percentage": percentage,
        "confidence": confidence_label(percentage),
    }


def confidence_label(percentage: float) -> str:
    if percentage >= 85:
        return "High"
    if percentage >= 60:
        return "Medium"
    return "Low"


def diagnostic_warnings(diagnostics: list[dict[str, str]]) -> list[str]:
    return [
        f"Data source: {item.get('source', 'unknown source')} failed "
        f"({item.get('kind', 'provider_error')}): {item.get('message', 'unknown error')}"
        for item in diagnostics
    ]
def has_statement_period_mismatch(data: MarketData) -> bool:
    latest: list[pd.Timestamp] = []
    for frame in (data.quarterly_income, data.quarterly_balance, data.quarterly_cashflow):
        if frame.empty:
            continue
        dates = pd.to_datetime(frame.columns, errors="coerce").dropna()
        if not dates.empty:
            latest.append(pd.Timestamp(dates.max()))
    return len(latest) > 1 and (max(latest) - min(latest)).days > 120
