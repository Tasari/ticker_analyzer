from __future__ import annotations

import os
from typing import Any

import pandas as pd

from ticker_analyzer.data_provider import MarketDataProvider, YFinanceProvider
from ticker_analyzer.data_quality import calculate_data_quality, freshness_score, observation_depth_score
from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData, StockAnalysis
from ticker_analyzer.metrics.builder import (
    apply_configured_metric_fallbacks,
    build_charts_data,
    build_raw_metrics,
)
from ticker_analyzer.metrics.formulas import is_financial_company
from ticker_analyzer.metrics.utils import clean_number
from ticker_analyzer.providers import CompositeProvider, SecClient, SecCompanyFactsProvider
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
        self.provider = provider or default_market_data_provider()
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

        profile = company_profile(data.info, ticker_symbol, data.official_ids, config)
        scoring_config = config_for_profile(config, profile)
        apply_peer_calibration(raw_metrics, data.info, profile, config)
        attach_metric_provenance(raw_metrics, data)
        tab_results, missing = self._score_tabs(raw_metrics, scoring_config)
        coverage = analysis_coverage(tab_results, scoring_config)
        data_quality, data_quality_breakdown = analysis_data_quality(
            tab_results, scoring_config, profile, data
        )
        overall_score = overall_score_with_missing_policy(tab_results, scoring_config)
        partial_note = partial_overall_note(tab_results, overall_score)
        if partial_note:
            missing.insert(0, partial_note)
        missing.extend(diagnostic_warnings(data.diagnostics))
        rating = calculate_overall_rating(
            overall_score,
            data_quality,
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
            # Kept as a compatibility alias for old snapshots/API consumers.
            confidence=data_quality,
            confidence_breakdown=data_quality_breakdown,
            data_quality=data_quality,
            data_quality_breakdown=data_quality_breakdown,
            scoring_version=4,
            config_version=int(config.get("version", 4)),
            calibration_version=str(config.get("calibration_version", "v4-bootstrap-2026Q3")),
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


def default_market_data_provider() -> MarketDataProvider:
    sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if sec_user_agent:
        return CompositeProvider([SecCompanyFactsProvider(SecClient(sec_user_agent)), YFinanceProvider()])
    return YFinanceProvider()


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


def analysis_data_quality(
    tab_results: dict[str, Any],
    config: dict[str, Any],
    profile: str,
    data: MarketData,
) -> tuple[float, dict[str, Any]]:
    weight_coverage = analysis_coverage(tab_results, config)["percentage"]
    financial_provenance = data.provenance.get("financials")
    filed_at = financial_provenance.filed_at if financial_provenance else None
    freshness = freshness_score(pd.Timestamp(filed_at) if filed_at else None)
    quarterly_observations = max(
        (len(frame.columns) for frame in (data.quarterly_income, data.quarterly_balance, data.quarterly_cashflow)),
        default=0,
    )
    annual_observations = max(
        (len(frame.columns) for frame in (data.annual_income, data.annual_balance, data.annual_cashflow)),
        default=0,
    )
    actual_observations = quarterly_observations or annual_observations * 4
    analyst_counts: list[float] = []
    for frame in (data.revenue_estimate, data.earnings_estimate):
        if not frame.empty and "numberOfAnalysts" in frame.columns:
            analyst_counts.extend(pd.to_numeric(frame["numberOfAnalysts"], errors="coerce").dropna().tolist())
    analysts = max(analyst_counts, default=0)
    analyst_score = 100 if analysts >= 20 else 85 if analysts >= 10 else 65 if analysts >= 5 else 40 if analysts >= 3 else 20 if analysts >= 1 else 0
    provenance_items = list(data.provenance.values())
    provenance_score = (
        sum(100 if item.is_primary_source else 35 if item.fallback_level == "estimated" else 60 for item in provenance_items)
        / len(provenance_items)
        if provenance_items else 0
    )
    secondary_fraction = (
        sum(not item.is_primary_source and item.fallback_level != "estimated" for item in provenance_items)
        / len(provenance_items)
        if provenance_items else 1
    )
    estimated_fraction = (
        sum(item.fallback_level == "estimated" for item in provenance_items) / len(provenance_items)
        if provenance_items else 0
    )
    providers = {item.provider.lower() for item in provenance_items}
    specialized = profile not in {"Industrial", "Financial"}
    has_regulatory = any(item.is_primary_source and item.provider.lower() in {"sec", "fdic", "nbp"} for item in provenance_items)
    score, breakdown = calculate_data_quality(
        metric_weight_coverage=weight_coverage,
        complete_tabs=sum(bool(result.get("complete")) for result in tab_results.values()),
        total_tabs=len(tab_results),
        filing_freshness=freshness,
        observation_depth=observation_depth_score(actual_observations),
        provenance_score=provenance_score,
        estimate_quality=analyst_score,
        profile_fit=55 if profile == "Financial" else 90 if specialized else 80,
        provider_errors=len(data.diagnostics),
        secondary_fraction=secondary_fraction,
        estimated_fraction=estimated_fraction,
        has_period_mismatch=has_statement_period_mismatch(data),
        yfinance_only=providers == {"yfinance"} or not providers,
        generic_financial=profile == "Financial",
        specialized_profile_without_regulatory_data=specialized and not has_regulatory,
        config=config,
    )
    breakdown.update({"analyst_count": analysts, "actual_observations": actual_observations})
    return score, breakdown


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


def company_profile(
    info: dict[str, Any],
    ticker: str = "",
    official_ids: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    overrides = (config or {}).get("profile_overrides", {})
    normalized_ticker = ticker.strip().upper()
    if normalized_ticker in overrides:
        return str(overrides[normalized_ticker])
    identifiers = official_ids or {}
    if identifiers.get("fdic_cert"):
        return "FinancialBank"
    if identifiers.get("finra_crd"):
        return "FinancialBroker"
    if identifiers.get("naic_code"):
        return "FinancialInsurance"
    industry = str(info.get("industry") or info.get("industryDisp") or "").lower()
    sector = str(info.get("sector") or "").lower()
    sic = str(identifiers.get("sec_sic") or identifiers.get("sic") or info.get("sic") or "")
    if "reit" in industry or sic.startswith("6798"):
        return "REIT"
    if sic.startswith("63") or any(term in industry for term in ("insurance", "reinsurance")):
        return "FinancialInsurance"
    if sic == "6282" or any(term in industry for term in ("asset management", "investment management")):
        return "FinancialAssetManager"
    if sic == "6211" or any(term in industry for term in ("capital markets", "broker", "securities")):
        return "FinancialBroker"
    if sic in {"6141", "6153", "6159", "6162", "6163"} or any(
        term in industry for term in ("credit services", "consumer finance", "mortgage finance")
    ):
        return "FinancialLender"
    if "bank" in industry or sic in {"6021", "6022", "6029", "6035", "6036"}:
        return "FinancialBank"
    if is_financial_company(info) or sector == "financial services":
        return "Financial"
    return "Industrial"


def config_for_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profile_metrics = config.get("profile_metrics", {}).get(profile)
    if not profile_metrics and profile.startswith("Financial"):
        profile_metrics = config.get("profile_metrics", {}).get("Financial")
    selected = dict(config)
    selected["active_profile"] = profile
    if profile_metrics:
        selected["metrics"] = profile_metrics
    selected["tab_groups"] = config.get("profile_tab_groups", {}).get(
        profile,
        config.get("profile_tab_groups", {}).get("Financial", config.get("tab_groups", {}))
        if profile.startswith("Financial") or profile == "REIT"
        else config.get("tab_groups", {}),
    )
    selected["active_profile_rules"] = config.get("profile_rules", {}).get(
        profile,
        config.get("profile_rules", {}).get("Financial", {})
        if profile.startswith("Financial") or profile == "REIT"
        else config.get("profile_rules", {}).get("Industrial", {}),
    )
    return selected


def attach_metric_provenance(raw_metrics: dict[str, dict[str, Any]], data: MarketData) -> None:
    estimate_metrics = {
        "revenue_estimate_growth",
        "eps_estimate_avg_growth",
        "price_target",
        "upside_vs_configured_benchmark",
    }
    price_metrics = {
        "price_change",
        "ps_vs_selected_median",
        "pe_vs_selected_median",
        "pb_vs_selected_median",
        "ev_ebitda_vs_selected_median",
        "price_to_cfo_vs_selected_median",
        "fcf_yield",
        "fcf_yield_ttm",
        "pe_vs_profile_median",
        "ev_ebitda_vs_profile_median",
        "fcf_yield_vs_profile_median",
        "valuation_growth_adjustment",
    }
    for metric_id, raw in raw_metrics.items():
        source = "estimates" if metric_id in estimate_metrics else "prices" if metric_id in price_metrics else "financials"
        provenance = data.provenance.get(source)
        if provenance is None:
            provenance = DataProvenance(
                provider="unavailable",
                observation_count=0,
                fallback_level="estimated",
                is_primary_source=False,
            )
        raw["provenance"] = provenance.as_dict()


def apply_peer_calibration(
    raw_metrics: dict[str, dict[str, Any]],
    info: dict[str, Any],
    profile: str,
    config: dict[str, Any],
) -> None:
    medians = config.get("peer_medians", {}).get(profile) or config.get("peer_medians", {}).get("Financial" if profile.startswith("Financial") else profile, {})
    comparisons = {
        "pe_vs_profile_median": (clean_number(info.get("trailingPE")), clean_number(medians.get("pe")), False),
        "ev_ebitda_vs_profile_median": (
            clean_number(info.get("enterpriseToEbitda")),
            clean_number(medians.get("ev_ebitda")),
            False,
        ),
        "fcf_yield_vs_profile_median": (
            clean_number(raw_metrics.get("fcf_yield_ttm", {}).get("value")),
            clean_number(medians.get("fcf_yield")),
            True,
        ),
    }
    for metric_id, (current, peer, higher_is_better) in comparisons.items():
        if current is None or peer in (None, 0):
            continue
        relative = (current / peer - 1) * 100
        raw_metrics[metric_id] = {
            "value": relative,
            "note": f"Compared with versioned {profile} peer median; "
            + ("positive is better" if higher_is_better else "negative is cheaper"),
        }


def has_statement_period_mismatch(data: MarketData) -> bool:
    latest: list[pd.Timestamp] = []
    for frame in (data.quarterly_income, data.quarterly_balance, data.quarterly_cashflow):
        if frame.empty:
            continue
        dates = pd.to_datetime(frame.columns, errors="coerce").dropna()
        if not dates.empty:
            latest.append(pd.Timestamp(dates.max()))
    return len(latest) > 1 and (max(latest) - min(latest)).days > 120
