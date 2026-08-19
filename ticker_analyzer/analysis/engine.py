from __future__ import annotations

import os
from typing import Any

from ticker_analyzer.analysis.aggregation import (
    grouped_tab_score,
    is_empty_ticker_response,
    metric_coverage,
    overall_score_with_missing_policy,
    partial_overall_note,
    profile_cap_applies,
    weighted_tab_score_from_results,
    years_from_range,
)
from ticker_analyzer.analysis.profiles import company_profile, config_for_profile
from ticker_analyzer.analysis.provenance import apply_peer_calibration, attach_metric_provenance
from ticker_analyzer.analysis.quality import (
    analysis_coverage,
    analysis_data_quality,
    analysis_model_applicability,
    calculate_confidence,
    confidence_label,
    diagnostic_warnings,
    has_statement_period_mismatch,
)
from ticker_analyzer.data_provider import MarketDataProvider, YFinanceProvider
from ticker_analyzer.domain import AnalysisRanges, MarketData, StockAnalysis
from ticker_analyzer.metrics.builder import (
    apply_configured_metric_fallbacks,
    build_charts_data,
    build_raw_metrics,
)
from ticker_analyzer.metrics.utils import clean_number
from ticker_analyzer.providers import CompositeProvider, SecClient, SecCompanyFactsProvider
from ticker_analyzer.scoring import (
    ScoringEngine,
    calculate_rating_decision,
    rating_label,
)

__all__ = [
    "StockAnalysisEngine",
    "analyze_ticker",
    "default_market_data_provider",
    "years_from_range",
    "is_empty_ticker_response",
    "overall_score_with_missing_policy",
    "weighted_tab_score_from_results",
    "partial_overall_note",
    "metric_coverage",
    "grouped_tab_score",
    "profile_cap_applies",
    "analysis_data_quality",
    "analysis_model_applicability",
    "calculate_confidence",
    "analysis_coverage",
    "confidence_label",
    "diagnostic_warnings",
    "company_profile",
    "config_for_profile",
    "attach_metric_provenance",
    "apply_peer_calibration",
    "has_statement_period_mismatch",
]


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
        model_applicability, applicability_warnings = analysis_model_applicability(
            scoring_config, profile, data
        )
        overall_score = overall_score_with_missing_policy(tab_results, scoring_config)
        partial_note = partial_overall_note(tab_results, overall_score)
        if partial_note:
            missing.insert(0, partial_note)
        if scoring_config.get("active_metric_model") == "generic_financial_fallback":
            missing.insert(
                0,
                "Profile: specialized regulatory metrics unavailable; generic financial fallback is capped at Buy.",
            )
        missing.extend(diagnostic_warnings(data.diagnostics))
        warnings = list(applicability_warnings)
        if partial_note:
            warnings.append(partial_note)
            warnings.extend(
                f"{name} assessment incomplete"
                for name, result in tab_results.items()
                if result.get("score") is None
            )
        if data_quality_breakdown.get("components", {}).get("cross_source_reconciliation") is None:
            warnings.append("Cross-source reconciliation unavailable")
        warnings.extend(diagnostic_warnings(data.diagnostics))
        decision = calculate_rating_decision(
            overall_score,
            data_quality,
            {name: result.get("score") for name, result in tab_results.items()},
            scoring_config,
            model_applicability=model_applicability,
            profile_rating_cap=scoring_config.get("active_rating_cap"),
        )
        rating_code = decision["rating_code"]
        rating = rating_label(rating_code, scoring_config)

        return StockAnalysis(
            ticker=ticker_symbol,
            company_name=data.info.get("longName") or data.info.get("shortName") or ticker_symbol,
            currency=data.info.get("currency", ""),
            profile=profile,
            current_price=clean_number(data.info.get("currentPrice") or data.info.get("regularMarketPrice")),
            overall_score=overall_score,
            rating=rating,
            rating_code=rating_code,
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
            model_applicability=model_applicability,
            rating_confidence=decision["rating_confidence"],
            rating_status=("insufficient_data" if rating_code == "insufficient_data" else "rated"),
            rating_caps=decision["rating_caps"],
            rating_reason_codes=decision["rating_reason_codes"],
            warnings=warnings,
            scoring_version=5,
            config_version=int(config.get("version", 5)),
            calibration_version=str(config.get("calibration_version", "v5.1-calibration-2026Q3")),
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
            coverage = metric_coverage(metric_results, tab_name, config)
            full_confidence = float(
                config.get("coverage_policy", {})
                .get("minimum_for_full_confidence", {})
                .get(tab_name, 0.80)
            ) * 100
            minimum_confidence = float(
                config.get("coverage_policy", {})
                .get("minimum_to_score", {})
                .get(tab_name, config.get("minimum_weight_coverage", {}).get(tab_name, 0))
            ) * 100
            coverage["confidence"] = (
                "High" if coverage["percentage"] >= full_confidence
                else "Medium" if coverage["percentage"] >= minimum_confidence
                else "Low"
            )
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
