from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.data_provider import YFinanceProvider
from ticker_analyzer.domain import AnalysisRanges, MarketData, StockAnalysis
# Re-export metric helpers here to preserve the original public import surface.
from ticker_analyzer.metric_builder import *
from ticker_analyzer.metric_formulas import *
from ticker_analyzer.metric_utils import *
from ticker_analyzer.scoring import ScoringEngine
from ticker_analyzer.valuation_metrics import *


def analyze_ticker(ticker_symbol: str, ranges: str | dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    engine = StockAnalysisEngine()
    return engine.analyze(ticker_symbol, ranges, config).as_dict()


class StockAnalysisEngine:
    def __init__(
        self,
        provider: YFinanceProvider | None = None,
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
            earnings_dates=data.earnings_dates,
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
        overall_score = overall_score_with_missing_policy(tab_results, scoring_config)
        partial_note = partial_overall_note(tab_results, overall_score)
        if partial_note:
            missing.insert(0, partial_note)
        rating = self.scoring.classify_rating(overall_score, config)
        if partial_note and rating != "Not Rated":
            rating = f"Partial {rating}"

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
            tab_score = self.scoring.weighted_score(metric_results)
            tab_results[tab_name] = {
                "score": tab_score,
                "rating": self.scoring.classify_tab_rating(tab_name, tab_score, config),
                "metrics": metric_results,
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
    return weighted_tab_score_from_results(tab_results, tab_weights)


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


def company_profile(info: dict[str, Any]) -> str:
    return "Financial" if is_financial_company(info) else "Industrial"


def config_for_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profile_metrics = config.get("profile_metrics", {}).get(profile)
    if not profile_metrics:
        return config
    selected = dict(config)
    selected["metrics"] = profile_metrics
    return selected
