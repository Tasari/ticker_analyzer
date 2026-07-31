from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.data_provider import MarketDataProvider, YFinanceProvider
from ticker_analyzer.domain import AnalysisRanges, MarketData, StockAnalysis
from ticker_analyzer.metrics.builder import (
    apply_configured_metric_fallbacks,
    build_charts_data,
    build_raw_metrics,
)
from ticker_analyzer.metrics.formulas import is_financial_company
from ticker_analyzer.metrics.utils import clean_number
from ticker_analyzer.scoring import ScoringEngine, calculate_overall_rating


def analyze_ticker(ticker_symbol: str, ranges: str | dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    engine = StockAnalysisEngine()
    return engine.analyze(ticker_symbol, ranges, config).as_dict()


class StockAnalysisEngine:
    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        scoring: ScoringEngine | None = None,
    ) -> None:
        self.provider = provider or YFinanceProvider()
        self.scoring = scoring or ScoringEngine()

    def analyze(self, ticker_symbol: str, ranges: str | dict[str, str], config: dict[str, Any]) -> StockAnalysis:
        ticker_symbol = ticker_symbol.strip().upper()
        if not ticker_symbol:
            raise ValueError("Enter a ticker symbol.")
        selected_ranges = AnalysisRanges.from_input(ranges)
        data = self.provider.fetch(ticker_symbol, selected_ranges)
        if not data.info and ticker_symbol:
            info_failure = next(
                (item for item in data.diagnostics if item.get("source") == "company info"),
                None,
            )
            if info_failure:
                raise ValueError(
                    f"Could not fetch company info for {ticker_symbol} "
                    f"({info_failure.get('kind', 'provider_error')}). Try again later."
                )
            raise ValueError(f"No data returned for {ticker_symbol}.")
        if self._is_empty_ticker_response(data):
            raise ValueError(f"No usable data returned for {ticker_symbol}. Check the ticker symbol and try again.")

        range_years = {
            tab_name: years_from_range(tab_range)
            for tab_name, tab_range in selected_ranges.as_dict().items()
        }
        raw_metrics = build_raw_metrics(
            info=data.info,
            annual_income=data.annual_income,
            annual_balance=data.annual_balance,
            annual_cashflow=data.annual_cashflow,
            quarterly_income=data.quarterly_income,
            quarterly_balance=data.quarterly_balance,
            quarterly_cashflow=data.quarterly_cashflow,
            growth_history=data.growth_history,
            value_history=data.value_history,
            analyst_targets=data.analyst_targets,
            revenue_estimate=data.revenue_estimate,
            earnings_estimate=data.earnings_estimate,
            eps_trend=data.eps_trend,
            growth_estimates=data.growth_estimates,
            range_years=range_years,
        )

        profile = company_profile(data.info)
        scoring_config = config_for_profile(config, profile)
        tab_results, missing = self._score_tabs(raw_metrics, scoring_config)
        coverage = analysis_coverage(tab_results, scoring_config)
        confidence, confidence_breakdown = calculate_confidence(
            tab_results, scoring_config, profile, selected_ranges, data
        )
        overall_score = overall_score_with_missing_policy(tab_results, scoring_config)
        partial_note = partial_overall_note(tab_results, overall_score)
        if partial_note:
            missing.insert(0, partial_note)
        missing.extend(diagnostic_warnings(data.diagnostics))
        rating = calculate_overall_rating(
            overall_score,
            confidence,
            {name: result.get("score") for name, result in tab_results.items()},
            config,
        )

        return StockAnalysis(
            ticker=ticker_symbol,
            company_name=data.info.get("longName") or data.info.get("shortName") or ticker_symbol,
            currency=data.info.get("currency", ""),
            profile=profile,
            current_price=clean_number(data.info.get("currentPrice") or data.info.get("regularMarketPrice")),
            overall_score=overall_score,
            rating=rating,
            tabs=tab_results,
            missing=missing,
            raw=raw_metrics,
            ranges=selected_ranges.as_dict(),
            charts=build_charts_data(data.annual_income, data.annual_cashflow, data.annual_balance, data.growth_history),
            coverage=coverage,
            confidence=confidence,
            confidence_breakdown=confidence_breakdown,
            scoring_version=3,
            config_version=int(config.get("version", 3)),
            diagnostics=data.diagnostics,
        )

    def _score_tabs(self, raw_metrics: dict[str, dict[str, Any]], config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        tab_results: dict[str, Any] = {}
        missing: list[str] = []
        for tab_name, metric_configs in config.get("metrics", {}).items():
            configured_raw_metrics = apply_configured_metric_fallbacks(raw_metrics, metric_configs)
            metric_results = [
                self.scoring.score_metric(metric_config, configured_raw_metrics, tab_name, config)
                for metric_config in metric_configs
            ]
            coverage = metric_coverage(metric_results)
            tab_score, group_breakdown = grouped_tab_score(tab_name, metric_results, config, coverage)
            if profile_cap_applies(config, tab_name):
                tab_score = min(tab_score, 85.0) if tab_score is not None else None
                group_breakdown.setdefault("caps", []).append("generic_financial_fundamentals")
            tab_results[tab_name] = {
                "score": tab_score,
                "rating": self.scoring.classify_tab_rating(tab_name, tab_score, config),
                "metrics": metric_results,
                "coverage": coverage,
                "group_breakdown": group_breakdown,
                "complete": tab_score is not None,
            }
            missing.extend(
                f"{tab_name}: {metric.name} ({metric.note or 'data unavailable'})"
                for metric in metric_results
                if metric.score is None
            )
        return tab_results, missing

    def _is_empty_ticker_response(self, data: MarketData) -> bool:
        return is_empty_ticker_response(
            data.info,
            data.annual_income,
            data.annual_balance,
            data.annual_cashflow,
            data.growth_history,
        )


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
    minimum_scored_tabs = int(clean_number(policy.get("minimum_scored_tabs")) or 1)
    if len(scored_tabs) < minimum_scored_tabs:
        return None
    weighted_mean = weighted_tab_score_from_results(tab_results, tab_weights)
    if weighted_mean is None:
        return None
    available_scores = [float(result["score"]) for result in tab_results.values() if result.get("score") is not None]
    if len(available_scores) < len(tab_results):
        return weighted_mean
    weakest = min(available_scores)
    return 0.80 * weighted_mean + 0.20 * weakest


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


def metric_coverage(metrics: list[Any]) -> dict[str, Any]:
    scored = [metric for metric in metrics if metric.weight > 0 and metric.score is not None]
    eligible = [metric for metric in metrics if metric.weight > 0]
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
    minimum = float(config.get("minimum_weight_coverage", {}).get(tab_name, 0)) * 100
    by_id = {metric.id: metric for metric in metrics}
    available_ids = {metric.id for metric in metrics if metric.score is not None and metric.weight > 0}
    groups = config.get("tab_groups", {}).get(tab_name, {})
    breakdown: dict[str, Any] = {"minimum_coverage": minimum, "groups": {}, "caps": []}
    if coverage["percentage"] + 1e-9 < minimum:
        breakdown["reason"] = "minimum_weight_coverage"
        return None, breakdown

    # Composition gates prevent a correlated family from standing in for a whole tab.
    if tab_name == "Growth":
        historical = {"revenue_ttm_range_growth", "net_income_range_growth", "cfo_range_growth"}
        forward_trend = {"operating_margin_trend", "gross_margin_trend", "revenue_estimate_growth", "eps_estimate_avg_growth"}
        if not available_ids & historical or not available_ids & forward_trend:
            breakdown["reason"] = "growth_composition"
            return None, breakdown
    elif tab_name == "Fundamentals" and config.get("active_profile") != "Financial":
        solvency = {"debt_to_assets", "quick_ratio", "cfo_to_debt", "interest_coverage", "net_debt_to_ebitda"}
        quality = {"roic", "fcf_margin", "accruals_ratio", "operating_margin"}
        if not available_ids & solvency or len(available_ids & quality) < 2:
            breakdown["reason"] = "fundamentals_composition"
            return None, breakdown
    elif tab_name == "Value":
        historical = {"ps_vs_selected_median", "pe_vs_selected_median", "pb_vs_selected_median", "ev_ebitda_vs_selected_median", "price_to_cfo_vs_selected_median"}
        independent = {"fcf_yield", "price_target"}
        if not available_ids & historical or not available_ids & independent:
            breakdown["reason"] = "value_composition"
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
        breakdown["groups"][group_name] = {"score": group_score, "weight": group_weight}
        if group_score is not None and group_weight > 0:
            scored_groups.append((group_score, group_weight))
    total_group_weight = sum(weight for _, weight in scored_groups)
    if total_group_weight <= 0:
        return None, breakdown
    score = sum(score * weight for score, weight in scored_groups) / total_group_weight
    if tab_name == "Fundamentals" and config.get("active_profile") != "Financial":
        solvency_score = breakdown["groups"].get("solvency", {}).get("score")
        quality_score = breakdown["groups"].get("quality", {}).get("score")
        if solvency_score is not None and quality_score is not None:
            score = 0.40 * min(float(solvency_score), 90.0) + 0.60 * float(quality_score)
            if float(solvency_score) > 90:
                breakdown["caps"].append("solvency_correlation_cap")
    return score, breakdown


def profile_cap_applies(config: dict[str, Any], tab_name: str) -> bool:
    return config.get("active_profile") == "Financial" and tab_name == "Fundamentals"


def calculate_confidence(
    tab_results: dict[str, Any],
    config: dict[str, Any],
    profile: str,
    ranges: AnalysisRanges,
    data: MarketData,
) -> tuple[float, dict[str, Any]]:
    weight_coverage = analysis_coverage(tab_results, config)["percentage"]
    dates = []
    for frame in (data.annual_income, data.annual_balance, data.annual_cashflow):
        if not frame.empty:
            dates.extend(pd.to_datetime(frame.columns, errors="coerce").dropna().tolist())
    age_days = max(0, (pd.Timestamp.now(tz=None).normalize() - max(dates)).days) if dates else 9999
    freshness = 100 if age_days <= 90 else 85 if age_days <= 180 else 65 if age_days <= 270 else 40 if age_days <= 365 else 10
    years = min(years_from_range(value) for value in ranges.as_dict().values())
    history = 100 if years >= 5 else 80 if years >= 3 else 60 if years >= 2 else 35
    analyst_counts: list[float] = []
    for frame in (data.revenue_estimate, data.earnings_estimate):
        if not frame.empty and "numberOfAnalysts" in frame.columns:
            analyst_counts.extend(pd.to_numeric(frame["numberOfAnalysts"], errors="coerce").dropna().tolist())
    analysts = max(analyst_counts, default=0)
    analyst_score = 100 if analysts >= 20 else 85 if analysts >= 10 else 65 if analysts >= 5 else 40 if analysts >= 3 else 20 if analysts >= 1 else 0
    profile_fit = 60 if profile == "Financial" else 80
    confidence = 0.50 * weight_coverage + 0.20 * freshness + 0.15 * history + 0.10 * analyst_score + 0.05 * profile_fit
    caps: list[str] = []
    if profile == "Financial":
        confidence = min(confidence, 80.0)
        caps.append("generic_financial_profile")
    confidence = max(0.0, min(confidence, 95.0))
    return confidence, {
        "weight_coverage": weight_coverage,
        "freshness": freshness,
        "freshness_age_days": age_days,
        "history_length": history,
        "analyst_coverage": analyst_score,
        "analyst_count": analysts,
        "profile_fit": profile_fit,
        "caps": caps,
    }


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


def company_profile(info: dict[str, Any]) -> str:
    return "Financial" if is_financial_company(info) else "Industrial"


def config_for_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profile_metrics = config.get("profile_metrics", {}).get(profile)
    selected = dict(config)
    selected["active_profile"] = profile
    if profile_metrics:
        selected["metrics"] = profile_metrics
    return selected
