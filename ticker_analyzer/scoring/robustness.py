from __future__ import annotations

import hashlib
import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from statistics import mean
from typing import Any

from ticker_analyzer.analysis.aggregation import (
    grouped_tab_score,
    metric_coverage,
    overall_score_with_missing_policy,
    profile_cap_applies,
)
from ticker_analyzer.analysis.profiles import config_for_profile
from ticker_analyzer.analysis.quality import analysis_coverage
from ticker_analyzer.domain import MetricResult
from ticker_analyzer.numbers import clean_number
from ticker_analyzer.scoring.quality import calculate_data_quality
from ticker_analyzer.scoring.ratings import calculate_rating_decision


class RobustnessAuditError(ValueError):
    """Raised when analysis results cannot support a metric-dropout audit."""


@dataclass(frozen=True)
class PerturbedAnalysis:
    ticker: str
    profile: str
    overall_score: float | None
    rating_code: str
    data_quality: float
    tab_scores: dict[str, float | None]
    dropped_metrics: tuple[str, ...]


def audit_scoring_robustness(
    results: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    dropout_rates: tuple[float, ...] = (0.10, 0.20),
    trials: int = 100,
    seed: int = 20260822,
) -> dict[str, Any]:
    if trials < 1:
        raise ValueError("trials must be positive")
    if not dropout_rates or any(rate <= 0 or rate >= 1 for rate in dropout_rates):
        raise ValueError("dropout rates must be between 0 and 1")
    prepared = sorted(results, key=lambda item: str(item.get("ticker") or ""))
    if len(prepared) < 2:
        raise RobustnessAuditError("At least two full company analyses are required.")
    _validate_replay(prepared, config)

    report: dict[str, Any] = {
        "method": {
            "dropout": "exact random removal of available positive-weight metrics per company",
            "data_quality": "recomputed from perturbed coverage when component breakdown is available",
            "rank_correlation": "Spearman with average ranks for ties",
            "seed": seed,
            "trials": trials,
        },
        "sample": _sample_summary(prepared),
        "dropout_rates": {},
    }
    for rate in dropout_rates:
        segment_trials: dict[str, list[dict[str, Any]]] = defaultdict(list)
        transition_counts: dict[str, Counter[str]] = defaultdict(Counter)
        metric_impacts: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"drops": 0, "unavailable": 0, "rating_flips": 0, "score_deltas": []}
        )
        company_impacts: dict[str, dict[str, Any]] = {
            str(result.get("ticker") or "Unknown"): {
                "profile": str(result.get("profile") or "Unknown"),
                "unavailable": 0,
                "rating_flips": 0,
                "score_deltas": [],
                "rank_deltas": [],
            }
            for result in prepared
        }
        for trial in range(trials):
            perturbed = [
                perturb_analysis(
                    result,
                    config,
                    dropout_rate=rate,
                    seed=_derived_seed(seed, rate, trial, str(result.get("ticker") or "")),
                )
                for result in prepared
            ]
            for segment in _segments(prepared):
                indices = [
                    index
                    for index, result in enumerate(prepared)
                    if _belongs_to_segment(str(result.get("profile") or "Unknown"), segment)
                ]
                if not indices:
                    continue
                summary, transitions = _trial_summary(prepared, perturbed, indices)
                segment_trials[segment].append(summary)
                transition_counts[segment].update(transitions)
            baseline_scores = {
                index: score
                for index, original in enumerate(prepared)
                if (score := clean_number(original.get("overall_score"))) is not None
            }
            changed_scores = {
                index: changed.overall_score
                for index, changed in enumerate(perturbed)
                if changed.overall_score is not None
            }
            joint = set(baseline_scores) & set(changed_scores)
            baseline_ranks = _rank_map({index: baseline_scores[index] for index in joint})
            changed_ranks = _rank_map({index: float(changed_scores[index]) for index in joint})
            for index, (original, changed) in enumerate(zip(prepared, perturbed, strict=True)):
                baseline = clean_number(original.get("overall_score"))
                baseline_rating = str(original.get("rating_code") or "insufficient_data")
                ticker = changed.ticker
                company = company_impacts[ticker]
                rating_flipped = baseline_rating != changed.rating_code
                company["rating_flips"] += rating_flipped
                if baseline is not None and changed.overall_score is None:
                    company["unavailable"] += 1
                if baseline is not None and changed.overall_score is not None:
                    delta = abs(changed.overall_score - baseline)
                    company["score_deltas"].append(delta)
                    company["rank_deltas"].append(
                        abs(changed_ranks[index] - baseline_ranks[index])
                    )
                for metric_id in changed.dropped_metrics:
                    metric = metric_impacts[metric_id]
                    metric["drops"] += 1
                    metric["rating_flips"] += rating_flipped
                    metric["unavailable"] += baseline is not None and changed.overall_score is None
                    if baseline is not None and changed.overall_score is not None:
                        metric["score_deltas"].append(abs(changed.overall_score - baseline))
        report["dropout_rates"][f"{rate:.0%}"] = {
            "segments": {
                segment: _aggregate_trials(summaries, transition_counts[segment])
                for segment, summaries in segment_trials.items()
            },
            "company_stability": _company_stability(company_impacts, trials),
            "most_sensitive_dropped_metrics": _metric_stability(metric_impacts),
        }
    return report


def perturb_analysis(
    result: dict[str, Any],
    config: dict[str, Any],
    *,
    dropout_rate: float,
    seed: int,
) -> PerturbedAnalysis:
    profile = str(result.get("profile") or "Industrial")
    scoring_config = config_for_profile(config, profile)
    metrics_by_tab = {
        tab_name: [_metric_result(metric) for metric in tab_result.get("metrics", [])]
        for tab_name, tab_result in result.get("tabs", {}).items()
    }
    available = [
        (tab_name, index, metric)
        for tab_name, metrics in metrics_by_tab.items()
        for index, metric in enumerate(metrics)
        if metric.score is not None
        and metric.weight > 0
        and _eligible_for_dropout(tab_name, metric.id, scoring_config)
    ]
    if not available:
        raise RobustnessAuditError(
            f"{result.get('ticker', 'Unknown')} has no metric-level scores; "
            "use full analyzer results rather than a compact ranking snapshot."
        )
    remove_count = min(len(available), max(1, round(len(available) * dropout_rate)))
    selected = random.Random(seed).sample(available, remove_count)
    dropped: list[str] = []
    for tab_name, index, metric in selected:
        metrics_by_tab[tab_name][index] = replace(
            metric,
            score=None,
            status="Unavailable",
            note="Removed by robustness audit",
        )
        dropped.append(f"{tab_name}.{metric.id}")

    tab_results: dict[str, dict[str, Any]] = {}
    for tab_name, metrics in metrics_by_tab.items():
        coverage = metric_coverage(metrics, tab_name, scoring_config)
        score, breakdown = grouped_tab_score(tab_name, metrics, scoring_config, coverage)
        if profile_cap_applies(scoring_config, tab_name):
            score = min(score, 85.0) if score is not None else None
        tab_results[tab_name] = {
            "score": score,
            "metrics": metrics,
            "coverage": coverage,
            "group_breakdown": breakdown,
        }
    overall = overall_score_with_missing_policy(tab_results, scoring_config)
    data_quality = _perturbed_data_quality(result, tab_results, scoring_config)
    tab_scores = {name: tab.get("score") for name, tab in tab_results.items()}
    decision = calculate_rating_decision(
        overall,
        data_quality,
        tab_scores,
        scoring_config,
        model_applicability=float(result.get("model_applicability", 100) or 0),
        profile_rating_cap=scoring_config.get("active_rating_cap"),
    )
    return PerturbedAnalysis(
        ticker=str(result.get("ticker") or "Unknown"),
        profile=profile,
        overall_score=overall,
        rating_code=str(decision["rating_code"]),
        data_quality=data_quality,
        tab_scores=tab_scores,
        dropped_metrics=tuple(sorted(dropped)),
    )


def compact_analysis_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the metric-level, JSON-safe subset needed by the audit."""
    return {
        key: result.get(key)
        for key in (
            "ticker",
            "company_name",
            "profile",
            "overall_score",
            "rating",
            "rating_code",
            "data_quality",
            "data_quality_breakdown",
            "model_applicability",
        )
    } | {
        "tabs": {
            tab_name: {
                "score": tab.get("score"),
                "coverage": tab.get("coverage", {}),
                "metrics": [
                    asdict(metric) if isinstance(metric, MetricResult) else dict(metric)
                    for metric in tab.get("metrics", [])
                ],
            }
            for tab_name, tab in result.get("tabs", {}).items()
        }
    }


def _validate_replay(results: list[dict[str, Any]], config: dict[str, Any]) -> None:
    for result in results:
        profile = str(result.get("profile") or "Industrial")
        scoring_config = config_for_profile(config, profile)
        replay_tabs: dict[str, dict[str, Any]] = {}
        metric_count = 0
        for tab_name, tab in result.get("tabs", {}).items():
            metrics = [_metric_result(metric) for metric in tab.get("metrics", [])]
            metric_count += len(metrics)
            coverage = metric_coverage(metrics, tab_name, scoring_config)
            score, _ = grouped_tab_score(tab_name, metrics, scoring_config, coverage)
            if profile_cap_applies(scoring_config, tab_name):
                score = min(score, 85.0) if score is not None else None
            replay_tabs[tab_name] = {"score": score, "coverage": coverage, "metrics": metrics}
        if not metric_count:
            raise RobustnessAuditError(
                f"{result.get('ticker', 'Unknown')} has no metric-level results."
            )
        replay = overall_score_with_missing_policy(replay_tabs, scoring_config)
        baseline = clean_number(result.get("overall_score"))
        if (baseline is None) != (replay is None) or (
            baseline is not None and replay is not None and abs(baseline - replay) > 1e-6
        ):
            raise RobustnessAuditError(
                f"{result.get('ticker', 'Unknown')} cannot be reproduced with the current config "
                f"(stored={baseline}, replayed={replay})."
            )


def _perturbed_data_quality(
    result: dict[str, Any],
    tab_results: dict[str, Any],
    config: dict[str, Any],
) -> float:
    original = float(result.get("data_quality", 0) or 0)
    breakdown = result.get("data_quality_breakdown", {})
    components = breakdown.get("components", {}) if isinstance(breakdown, dict) else {}
    required = {"data_freshness", "source_quality", "effective_metric_coverage"}
    if not required.issubset(components):
        return original
    caps = set(breakdown.get("caps", []))
    score, _ = calculate_data_quality(
        metric_weight_coverage=analysis_coverage(tab_results, config)["percentage"],
        filing_freshness=float(components.get("data_freshness") or 0),
        provenance_score=float(components.get("source_quality") or 0),
        reconciliation_score=components.get("cross_source_reconciliation"),
        source_mix=str(breakdown.get("source_mix") or "secondary_only"),
        has_period_mismatch="critical_period_mismatch" in caps,
        has_critical_mismatch="critical_source_mismatch" in caps,
        config=config,
    )
    return score


def _trial_summary(
    originals: list[dict[str, Any]],
    perturbed: list[PerturbedAnalysis],
    indices: list[int],
) -> tuple[dict[str, float], Counter[str]]:
    baseline_scores = {
        index: score
        for index in indices
        if (score := clean_number(originals[index].get("overall_score"))) is not None
    }
    changed_scores = {
        index: perturbed[index].overall_score
        for index in indices
        if perturbed[index].overall_score is not None
    }
    joint = sorted(set(baseline_scores) & set(changed_scores))
    baseline_ranks = _rank_map({index: baseline_scores[index] for index in joint})
    changed_ranks = _rank_map({index: float(changed_scores[index]) for index in joint})
    score_deltas = [abs(float(changed_scores[index]) - baseline_scores[index]) for index in joint]
    rank_deltas = [abs(changed_ranks[index] - baseline_ranks[index]) for index in joint]
    transitions: Counter[str] = Counter()
    flips = 0
    for index in indices:
        before = str(originals[index].get("rating_code") or "insufficient_data")
        after = perturbed[index].rating_code
        transitions[f"{before}->{after}"] += 1
        flips += before != after
    scored_baseline = len(baseline_scores)
    return {
        "sample_size": float(len(indices)),
        "jointly_scored": float(len(joint)),
        "spearman": _spearman_from_ranks(baseline_ranks, changed_ranks),
        "score_unavailable_pct": (
            (scored_baseline - len(joint)) / scored_baseline * 100 if scored_baseline else 0.0
        ),
        "rating_flip_pct": flips / len(indices) * 100,
        "mean_absolute_score_delta": mean(score_deltas) if score_deltas else math.nan,
        "mean_absolute_rank_change": mean(rank_deltas) if rank_deltas else math.nan,
        "p95_absolute_rank_change": _percentile(rank_deltas, 0.95),
    }, transitions


def _aggregate_trials(
    summaries: list[dict[str, float]],
    transitions: Counter[str],
) -> dict[str, Any]:
    def values(name: str) -> list[float]:
        return [value for item in summaries if math.isfinite(value := item[name])]

    return {
        "sample_size": int(summaries[0]["sample_size"]) if summaries else 0,
        "trials": len(summaries),
        "jointly_scored_mean": round(mean(values("jointly_scored")), 2),
        "spearman_mean": _rounded_mean(values("spearman")),
        "spearman_p05": _rounded(_percentile(values("spearman"), 0.05)),
        "score_unavailable_pct_mean": _rounded_mean(values("score_unavailable_pct")),
        "rating_flip_pct_mean": _rounded_mean(values("rating_flip_pct")),
        "mean_absolute_score_delta": _rounded_mean(values("mean_absolute_score_delta")),
        "mean_absolute_rank_change": _rounded_mean(values("mean_absolute_rank_change")),
        "p95_absolute_rank_change_mean": _rounded_mean(values("p95_absolute_rank_change")),
        "rating_transitions": dict(sorted(transitions.items())),
    }


def _sample_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    profiles = Counter(str(result.get("profile") or "Unknown") for result in results)
    return {
        "companies": len(results),
        "profiles": dict(sorted(profiles.items())),
        "industrial": profiles.get("Industrial", 0),
        "financial": sum(
            count
            for profile, count in profiles.items()
            if profile.startswith("Financial") or profile == "REIT"
        ),
    }


def _company_stability(
    impacts: dict[str, dict[str, Any]],
    trials: int,
) -> list[dict[str, Any]]:
    rows = [
        {
            "ticker": ticker,
            "profile": values["profile"],
            "rating_flip_pct": round(values["rating_flips"] / trials * 100, 4),
            "score_unavailable_pct": round(values["unavailable"] / trials * 100, 4),
            "mean_absolute_score_delta": _rounded_mean(values["score_deltas"]),
            "mean_absolute_rank_change": _rounded_mean(values["rank_deltas"]),
        }
        for ticker, values in impacts.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["rating_flip_pct"],
            row["score_unavailable_pct"],
            row["mean_absolute_score_delta"] or 0,
        ),
        reverse=True,
    )


def _metric_stability(impacts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {
            "metric": metric_id,
            "drops": values["drops"],
            "score_unavailable_pct": round(values["unavailable"] / values["drops"] * 100, 4),
            "rating_flip_pct": round(values["rating_flips"] / values["drops"] * 100, 4),
            "mean_absolute_overall_delta": _rounded_mean(values["score_deltas"]),
        }
        for metric_id, values in impacts.items()
        if values["drops"]
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["score_unavailable_pct"],
            row["rating_flip_pct"],
            row["mean_absolute_overall_delta"] or 0,
        ),
        reverse=True,
    )[:15]


def _segments(results: list[dict[str, Any]]) -> list[str]:
    profiles = {str(result.get("profile") or "Unknown") for result in results}
    ordered = ["All"]
    if "Industrial" in profiles:
        ordered.append("Industrial")
    if any(profile.startswith("Financial") or profile == "REIT" for profile in profiles):
        ordered.append("Financial")
    ordered.extend(sorted(profile for profile in profiles if profile not in {"Industrial", "Financial"}))
    return ordered


def _belongs_to_segment(profile: str, segment: str) -> bool:
    if segment == "All":
        return True
    if segment == "Financial":
        return profile.startswith("Financial") or profile == "REIT"
    return profile == segment


def _metric_result(metric: Any) -> MetricResult:
    if isinstance(metric, MetricResult):
        return replace(metric)
    if not isinstance(metric, dict):
        raise RobustnessAuditError("Metric results must be objects or dictionaries.")
    return MetricResult(
        id=str(metric.get("id") or "unknown"),
        name=str(metric.get("name") or metric.get("id") or "Unknown"),
        value=clean_number(metric.get("value")),
        unit=str(metric.get("unit") or ""),
        score=clean_number(metric.get("score")),
        weight=float(metric.get("weight") or 0),
        status=str(metric.get("status") or "Unavailable"),
        note=str(metric.get("note") or ""),
        description=str(metric.get("description") or ""),
        provenance=metric.get("provenance"),
    )


def _eligible_for_dropout(tab_name: str, metric_id: str, config: dict[str, Any]) -> bool:
    groups = config.get("tab_groups", {}).get(tab_name, {})
    if not groups:
        return True
    effective_ids = {
        item
        for definition in groups.values()
        if float(definition.get("weight", 0)) > 0
        for item in definition.get("metrics", [])
    }
    return not effective_ids or metric_id in effective_ids


def _rank_map(values: dict[int, float]) -> dict[int, float]:
    ordered = sorted(values.items(), key=lambda item: item[1], reverse=True)
    ranks: dict[int, float] = {}
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and ordered[end][1] == ordered[position][1]:
            end += 1
        average_rank = ((position + 1) + end) / 2
        for index in range(position, end):
            ranks[ordered[index][0]] = average_rank
        position = end
    return ranks


def _spearman_from_ranks(left: dict[int, float], right: dict[int, float]) -> float:
    keys = sorted(set(left) & set(right))
    if len(keys) < 2:
        return math.nan
    left_values = [left[key] for key in keys]
    right_values = [right[key] for key in keys]
    left_mean = mean(left_values)
    right_mean = mean(right_values)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )
    left_variance = sum((value - left_mean) ** 2 for value in left_values)
    right_variance = sum((value - right_mean) ** 2 for value in right_values)
    denominator = math.sqrt(left_variance * right_variance)
    return numerator / denominator if denominator > 0 else math.nan


def _percentile(values: list[float], fraction: float) -> float:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return math.nan
    position = (len(finite) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return finite[lower]
    weight = position - lower
    return finite[lower] * (1 - weight) + finite[upper] * weight


def _derived_seed(seed: int, rate: float, trial: int, ticker: str) -> int:
    digest = hashlib.sha256(f"{seed}:{rate:.8f}:{trial}:{ticker}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rounded(value: float) -> float | None:
    return round(value, 4) if math.isfinite(value) else None


def _rounded_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None
