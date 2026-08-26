from __future__ import annotations

from typing import Any

import pandas as pd


def freshness_score(filed_at: pd.Timestamp | None, now: pd.Timestamp | None = None) -> float:
    if filed_at is None or pd.isna(filed_at):
        return 0.0
    current = now or pd.Timestamp.now(tz="UTC")
    filed = pd.Timestamp(filed_at)
    filed = filed.tz_localize("UTC") if filed.tzinfo is None else filed.tz_convert("UTC")
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    age_days = max(0, (current - filed).days)
    for maximum_age, score in (
        (45, 100.0),
        (90, 90.0),
        (135, 75.0),
        (180, 60.0),
        (270, 40.0),
        (365, 20.0),
    ):
        if age_days <= maximum_age:
            return score
    return 5.0


def observation_depth_score(actual_observations: int, required_observations: int = 12) -> float:
    """Compatibility helper; observation depth is no longer a separate DQ penalty."""
    if required_observations <= 0:
        return 0.0
    return max(0.0, min(actual_observations / required_observations * 100, 100.0))


def calculate_data_quality(
    *,
    metric_weight_coverage: float,
    filing_freshness: float,
    provenance_score: float,
    reconciliation_score: float | None = None,
    source_mix: str = "secondary_only",
    has_period_mismatch: bool = False,
    has_critical_mismatch: bool = False,
    config: dict[str, Any] | None = None,
    **_legacy_inputs: Any,
) -> tuple[float, dict[str, Any]]:
    """Calculate v5.1 Data Quality independently from model applicability.

    Reconciliation is optional: when only one source exists its value is ``None``
    and the remaining component weights are renormalized. Legacy keyword inputs are
    accepted so downstream callers do not break, but no longer double-penalize DQ.
    """
    settings = (config or {}).get("data_quality", {})
    weights = {
        "effective_metric_coverage": 0.50,
        "data_freshness": 0.25,
        "source_quality": 0.15,
        "cross_source_reconciliation": 0.10,
        **settings.get("component_weights", settings.get("weights", {})),
    }
    components: dict[str, float | None] = {
        "effective_metric_coverage": _bounded(metric_weight_coverage),
        "data_freshness": _bounded(filing_freshness),
        "source_quality": _bounded(provenance_score),
        "cross_source_reconciliation": (
            None if reconciliation_score is None else _bounded(reconciliation_score)
        ),
    }
    available = {
        name: (value, float(weights[name]))
        for name, value in components.items()
        if value is not None and float(weights.get(name, 0)) > 0
    }
    denominator = sum(weight for _, weight in available.values())
    score = (
        sum(float(value) * weight for value, weight in available.values()) / denominator
        if denominator > 0
        else 0.0
    )

    maximum = float(settings.get("maximum", 95))
    caps: list[str] = []
    source_caps = {
        "secondary_only": 75.0,
        "primary_only": 85.0,
        "primary_and_secondary": 92.0,
    }
    if source_mix in source_caps:
        maximum = min(maximum, source_caps[source_mix])
        caps.append(source_mix)
    if has_period_mismatch:
        maximum = min(maximum, 55.0)
        caps.append("critical_period_mismatch")
    if has_critical_mismatch:
        maximum = min(maximum, 55.0)
        caps.append("critical_source_mismatch")
    score = max(0.0, min(score, maximum))
    normalized_weights = {
        name: weight / denominator for name, (_, weight) in available.items()
    } if denominator > 0 else {}
    return score, {
        "components": components,
        "available_components": list(available),
        "normalized_weights": normalized_weights,
        "penalties": {},
        "caps": caps,
        "maximum": maximum,
        "source_mix": source_mix,
    }


def _bounded(value: float) -> float:
    return max(0.0, min(float(value), 100.0))
