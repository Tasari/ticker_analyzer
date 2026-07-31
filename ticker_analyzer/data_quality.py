from __future__ import annotations

from typing import Any

import pandas as pd


def freshness_score(filed_at: pd.Timestamp | None, now: pd.Timestamp | None = None) -> float:
    if filed_at is None or pd.isna(filed_at):
        return 0.0
    current = now or pd.Timestamp.now(tz="UTC")
    filed = pd.Timestamp(filed_at)
    if filed.tzinfo is None:
        filed = filed.tz_localize("UTC")
    else:
        filed = filed.tz_convert("UTC")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    age_days = max(0, (current - filed).days)
    if age_days <= 45:
        return 100.0
    if age_days <= 90:
        return 90.0
    if age_days <= 135:
        return 75.0
    if age_days <= 180:
        return 60.0
    if age_days <= 270:
        return 40.0
    if age_days <= 365:
        return 20.0
    return 5.0


def observation_depth_score(actual_observations: int, required_observations: int = 12) -> float:
    if required_observations <= 0:
        return 0.0
    return max(0.0, min(actual_observations / required_observations * 100, 100.0))


def calculate_data_quality(
    *,
    metric_weight_coverage: float,
    complete_tabs: int,
    total_tabs: int,
    filing_freshness: float,
    observation_depth: float,
    provenance_score: float,
    estimate_quality: float,
    profile_fit: float,
    provider_errors: int,
    secondary_fraction: float,
    estimated_fraction: float,
    has_period_mismatch: bool,
    yfinance_only: bool,
    generic_financial: bool,
    production_mode: bool = False,
    manual_override_without_evidence: bool = False,
    specialized_profile_without_regulatory_data: bool = False,
    reconciliation_score: float = 100.0,
    has_critical_mismatch: bool = False,
    config: dict[str, Any] | None = None,
) -> tuple[float, dict[str, Any]]:
    settings = (config or {}).get("data_quality", {})
    weights = {
        "metric_weight_coverage": 0.20,
        "tab_completeness": 0.10,
        "filing_freshness": 0.15,
        "actual_observation_depth": 0.10,
        "source_provenance": 0.15,
        "cross_source_reconciliation": 0.10,
        "temporal_alignment": 0.10,
        "estimate_quality": 0.05,
        "profile_fit": 0.05,
        **settings.get("weights", {}),
    }
    tab_completeness = complete_tabs / total_tabs * 100 if total_tabs else 0.0
    components = {
        "metric_weight_coverage": metric_weight_coverage,
        "tab_completeness": tab_completeness,
        "filing_freshness": filing_freshness,
        "actual_observation_depth": observation_depth,
        "source_provenance": provenance_score,
        "cross_source_reconciliation": reconciliation_score,
        "temporal_alignment": 100.0 if not has_period_mismatch else 20.0,
        "estimate_quality": estimate_quality,
        "profile_fit": profile_fit,
    }
    score = sum(float(weights[name]) * value for name, value in components.items())
    penalties: dict[str, float] = {}
    provider_penalty = min(provider_errors * 3, 12)
    if provider_penalty:
        penalties["provider_errors"] = provider_penalty
    if secondary_fraction > 0:
        penalties["secondary_sources"] = 10 * secondary_fraction
    if estimated_fraction > 0:
        penalties["estimated_values"] = 15 * estimated_fraction
    if has_period_mismatch:
        penalties["period_mismatch"] = 8
    maximum = float(settings.get("maximum", 95))
    caps: list[str] = []
    if complete_tabs < total_tabs:
        maximum = min(maximum, 55.0)
        caps.append("incomplete_tab")
    if has_period_mismatch:
        maximum = min(maximum, 55.0)
        caps.append("critical_period_mismatch")
    if has_critical_mismatch:
        maximum = min(maximum, 55.0)
        caps.append("critical_source_mismatch")
    if yfinance_only:
        maximum = min(maximum, 70.0 if production_mode else 85.0)
        caps.append("yfinance_only")
    if generic_financial:
        maximum = min(maximum, 60.0)
        caps.append("generic_financial")
    if specialized_profile_without_regulatory_data:
        maximum = min(maximum, 65.0)
        caps.append("specialized_profile_without_regulatory_data")
    if manual_override_without_evidence:
        maximum = min(maximum, 75.0)
        caps.append("manual_override_without_evidence")
    # Caps express the maximum trust in the source mix. Explicit data problems
    # are then deducted so they remain visible instead of being hidden by a cap.
    score = max(0.0, min(score, maximum) - sum(penalties.values()))
    return score, {"components": components, "penalties": penalties, "caps": caps, "maximum": maximum}
