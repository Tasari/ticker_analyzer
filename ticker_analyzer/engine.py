from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ticker_analyzer.data_provider import YFinanceProvider
from ticker_analyzer.domain import AnalysisRanges, MarketData, StockAnalysis
from ticker_analyzer.scoring import ScoringEngine


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
            growth_history=data.growth_history,
            value_history=data.value_history,
            earnings_dates=data.earnings_dates,
            analyst_targets=data.analyst_targets,
            range_years=range_years,
        )

        tab_results, missing = self._score_tabs(raw_metrics, config)
        overall_score = self.scoring.weighted_tab_score(tab_results, config.get("tab_weights", {}))
        if any(result.get("score") is None for result in tab_results.values()):
            overall_score = None
        rating = self.scoring.classify_rating(overall_score, config)

        return StockAnalysis(
            ticker=ticker_symbol,
            company_name=data.info.get("longName") or data.info.get("shortName") or ticker_symbol,
            currency=data.info.get("currency", ""),
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
            return 3
    return 3


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
def build_raw_metrics(
    *,
    info: dict[str, Any],
    annual_income: pd.DataFrame,
    annual_balance: pd.DataFrame,
    annual_cashflow: pd.DataFrame,
    quarterly_income: pd.DataFrame,
    quarterly_balance: pd.DataFrame,
    growth_history: pd.DataFrame,
    value_history: pd.DataFrame,
    earnings_dates: pd.DataFrame,
    analyst_targets: dict[str, Any],
    range_years: dict[str, int],
) -> dict[str, dict[str, Any]]:
    revenue_ttm = sum_recent(quarterly_income, ["Total Revenue", "Operating Revenue"], 4)
    if revenue_ttm is None:
        revenue_ttm = clean_number(info.get("totalRevenue"))
    revenue_prior_ttm = sum_window(quarterly_income, ["Total Revenue", "Operating Revenue"], 4, 8)
    growth_years = range_years["Growth"]
    fundamentals_years = range_years["Fundamentals"]
    value_years = range_years["Value"]
    revenue_range_base = statement_value_years_ago(annual_income, ["Total Revenue", "Operating Revenue"], growth_years)
    net_income_range_base = statement_value_years_ago(annual_income, ["Net Income", "Net Income Common Stockholders"], growth_years)
    cfo_range_base = statement_value_years_ago(annual_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], growth_years)
    momentum = momentum_12_1(growth_history)

    revenue_estimate_growth = estimate_growth(info, "revenue")
    eps_estimate_growth = estimate_growth(info, "eps")
    price_target_upside = target_upside(info, analyst_targets)
    financial_company = is_financial_company(info)

    raw = {
        "revenue_ttm_range_growth": metric_value(cagr_pct(revenue_ttm, revenue_range_base, growth_years), f"Revenue TTM CAGR compared with annual revenue from {growth_years} fiscal year(s) ago"),
        "revenue_ttm_growth": metric_value(percent_change(revenue_ttm, revenue_prior_ttm), "TTM vs previous TTM"),
        "net_income_range_growth": metric_value(cagr_pct(latest_row_value(annual_income, ["Net Income", "Net Income Common Stockholders"]), net_income_range_base, growth_years), f"Latest annual net income CAGR over {growth_years} fiscal year(s); missing when the base or current value is not positive"),
        "cfo_range_growth": metric_value(cagr_pct(latest_row_value(annual_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]), cfo_range_base, growth_years), f"Latest annual operating cash flow CAGR over {growth_years} fiscal year(s); missing when the base or current value is not positive"),
        "operating_margin": metric_value(operating_margin(annual_income)),
        "price_change": metric_value(momentum, "Adjusted-price momentum from month -13 to month -2, closer to standard 12-1 momentum"),
        "revenue_estimate_growth": metric_value(revenue_estimate_growth, "Uses available yfinance analyst estimate fields"),
        "eps_estimate_avg_growth": metric_value(eps_estimate_growth, "Proxy from available yfinance estimate fields"),
        "debt_to_assets": financial_metric_value(debt_to_assets(annual_balance), financial_company, f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "quick_ratio": financial_metric_value(quick_ratio(info, quarterly_balance, annual_balance), financial_company, "Uses yfinance quickRatio, then balance sheet fallback"),
        "cfo_to_debt": financial_metric_value(cfo_to_debt(annual_cashflow, annual_balance), financial_company, f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "interest_coverage": financial_metric_value(interest_coverage(annual_income), financial_company, f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "ohlson_probability": financial_metric_value(ohlson_probability(annual_income, annual_balance, annual_cashflow), financial_company, "Ohlson-style distress estimate using annual statements; SIZE is approximated without a market price deflator"),
        "ps_vs_3y_median": financial_metric_value(ratio_vs_history(info.get("priceToSalesTrailing12Months"), "ps", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), financial_company, f"Compared with approximate {value_years}Y median using year-matched annual financials"),
        "pe_vs_3y_median": metric_value(ratio_vs_history(info.get("trailingPE"), "pe", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), f"Compared with approximate {value_years}Y median"),
        "ev_ebitda_vs_5y_median": financial_metric_value(ratio_vs_history(info.get("enterpriseToEbitda"), "ev_ebitda", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), financial_company, f"Compared with approximate {value_years}Y median using year-matched annual financials"),
        "price_to_cfo_vs_5y_median": financial_metric_value(ratio_vs_history(current_price_to_cfo(info, annual_cashflow), "price_to_cfo", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), financial_company, f"Compared with approximate {value_years}Y median using year-matched annual financials"),
        "price_target": metric_value(price_target_upside),
        "upside_vs_configured_benchmark": metric_value(None, "Uses configured benchmark because historical analyst upside is unavailable"),
    }
    return raw


def apply_configured_metric_fallbacks(
    raw_metrics: dict[str, dict[str, Any]],
    metric_configs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    updated = dict(raw_metrics)
    config_by_id = {metric_config.get("id"): metric_config for metric_config in metric_configs}
    upside_config = config_by_id.get("upside_vs_configured_benchmark")
    price_target_upside = clean_number(updated.get("price_target", {}).get("value"))
    benchmark = clean_number(upside_config.get("benchmark")) if upside_config else None
    if price_target_upside is not None and benchmark is not None:
        updated["upside_vs_configured_benchmark"] = metric_value(
            price_target_upside - benchmark,
            f"Current price target upside minus configured benchmark ({benchmark:.2f}%)",
        )
    return updated


def metric_value(value: float | None, note: str = "") -> dict[str, Any]:
    return {"value": clean_number(value), "note": note}


def financial_metric_value(value: float | None, is_financial: bool, note: str = "") -> dict[str, Any]:
    if is_financial:
        return metric_value(None, "Not applicable to financial companies under the default industrial scoring profile")
    return metric_value(value, note)


def is_financial_company(info: dict[str, Any]) -> bool:
    industry = str(info.get("industry") or info.get("industryDisp") or "").lower()
    quote_type = str(info.get("quoteType") or "").lower()
    financial_industries = [
        "bank",
        "insurance",
        "asset management",
        "capital markets",
        "mortgage",
        "reit",
    ]
    return quote_type == "equity" and any(keyword in industry for keyword in financial_industries)


def clean_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def scale_billions(value: Any) -> float | None:
    number = clean_number(value)
    if number is None:
        return None
    return number / 1_000_000_000


def row_values(frame: pd.DataFrame, names: list[str]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    lowered = {str(index).lower(): index for index in frame.index}
    for name in names:
        index = lowered.get(name.lower())
        if index is not None:
            values = pd.to_numeric(frame.loc[index], errors="coerce").dropna()
            return values
    return pd.Series(dtype=float)


def latest_row_value(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if values.empty:
        return None
    return clean_number(values.iloc[-1])


def latest_statement_growth(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if len(values) < 2:
        return None
    return percent_change(values.iloc[-1], values.iloc[-2])


def statement_value_years_ago(frame: pd.DataFrame, names: list[str], years_ago: int) -> float | None:
    values = row_values(frame, names)
    required_length = years_ago + 1
    if len(values) < required_length:
        return None
    return clean_number(values.iloc[-required_length])


def sum_recent(frame: pd.DataFrame, names: list[str], periods: int) -> float | None:
    values = row_values(frame, names)
    if len(values) < periods:
        return None
    return clean_number(values.iloc[-periods:].sum())


def sum_window(frame: pd.DataFrame, names: list[str], start: int, end: int) -> float | None:
    values = row_values(frame, names)
    if len(values) < end:
        return None
    return clean_number(values.iloc[-end:-start].sum())


def percent_change(current: Any, previous: Any) -> float | None:
    current = clean_number(current)
    previous = clean_number(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / abs(previous)) * 100


def cagr_pct(current: Any, previous: Any, years: int) -> float | None:
    current = clean_number(current)
    previous = clean_number(previous)
    if current is None or previous is None or years <= 0:
        return None
    if current <= 0 or previous <= 0:
        return None
    return ((current / previous) ** (1 / years) - 1) * 100


def percentage_change_from_history(history: pd.DataFrame) -> float | None:
    if history.empty or "Close" not in history:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2 or close.iloc[0] == 0:
        return None
    return percent_change(close.iloc[-1], close.iloc[0])


def momentum_12_1(history: pd.DataFrame) -> float | None:
    if history.empty or "Close" not in history:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2:
        return None
    monthly = close.resample("ME").last().dropna()
    if len(monthly) < 13:
        return percentage_change_from_history(history)
    return percent_change(monthly.iloc[-2], monthly.iloc[-13])


def operating_margin(income: pd.DataFrame) -> float | None:
    operating_income = latest_row_value(income, ["Operating Income"])
    revenue = latest_row_value(income, ["Total Revenue", "Operating Revenue"])
    if revenue in (None, 0) or operating_income is None:
        return None
    return operating_income / revenue * 100


def debt_to_assets(balance: pd.DataFrame) -> float | None:
    debt = latest_row_value(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
    assets = latest_row_value(balance, ["Total Assets"])
    if debt is None or assets in (None, 0):
        return None
    return debt / assets * 100


def quick_ratio(info: dict[str, Any], quarterly_balance: pd.DataFrame, annual_balance: pd.DataFrame) -> float | None:
    reported = clean_number(info.get("quickRatio"))
    if reported is not None:
        return reported
    balance = quarterly_balance if not quarterly_balance.empty else annual_balance
    cash_and_investments = latest_row_value(balance, ["Cash Cash Equivalents And Short Term Investments"])
    if cash_and_investments is None:
        cash = latest_row_value(balance, ["Cash And Cash Equivalents"]) or 0
        short_term_investments = latest_row_value(balance, ["Other Short Term Investments"]) or 0
        cash_and_investments = cash + short_term_investments
    receivables = latest_row_value(balance, ["Receivables", "Accounts Receivable"]) or 0
    liabilities = latest_row_value(balance, ["Current Liabilities", "Total Current Liabilities"])
    if liabilities in (None, 0):
        return None
    return (cash_and_investments + receivables) / liabilities


def cfo_to_debt(cashflow: pd.DataFrame, balance: pd.DataFrame) -> float | None:
    cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    debt = latest_row_value(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
    if cfo is None or debt is None:
        return None
    if debt <= 0:
        return 999.0 if cfo > 0 else None
    return cfo / debt


def interest_coverage(income: pd.DataFrame) -> float | None:
    operating_income = latest_row_value(income, ["Operating Income", "EBIT"])
    interest = latest_row_value(income, ["Interest Expense", "Interest Expense Non Operating"])
    if operating_income is None or interest in (None, 0):
        return None
    return operating_income / abs(interest)


def ohlson_probability(income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame) -> float | None:
    assets = latest_row_value(balance, ["Total Assets"])
    liabilities = latest_row_value(balance, ["Total Liabilities Net Minority Interest", "Total Liab"])
    current_assets = latest_row_value(balance, ["Current Assets", "Total Current Assets"])
    current_liabilities = latest_row_value(balance, ["Current Liabilities", "Total Current Liabilities"])
    net_income = latest_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    prior_net_income = prior_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    working_capital = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities
    required = [assets, liabilities, current_assets, current_liabilities, net_income, prior_net_income, cfo, working_capital]
    if any(value is None for value in required) or assets == 0:
        return None
    size = math.log(max(assets / 1_000_000, 1))
    tlta = liabilities / assets
    wcta = working_capital / assets
    clca = current_liabilities / current_assets if current_assets else None
    nita = net_income / assets
    futl = cfo / liabilities if liabilities else None
    intwo = 1 if net_income < 0 and prior_net_income < 0 else 0
    oeneg = 1 if liabilities > assets else 0
    chin = (net_income - prior_net_income) / (abs(net_income) + abs(prior_net_income))
    if clca is None or futl is None:
        return None
    score = -1.32 - 0.407 * size + 6.03 * tlta - 1.43 * wcta + 0.076 * clca - 1.72 * oeneg - 2.37 * nita - 1.83 * futl + 0.285 * intwo - 0.521 * chin
    return 1 / (1 + math.exp(-score)) * 100


def prior_row_value(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if len(values) < 2:
        return None
    return clean_number(values.iloc[-2])


def estimate_growth(info: dict[str, Any], kind: str) -> float | None:
    if kind == "revenue":
        current = clean_number(info.get("revenueCurrentYear"))
        next_year = clean_number(info.get("revenueNextYear"))
        growth = clean_number(info.get("revenueGrowth"))
    else:
        current = clean_number(info.get("epsCurrentYear"))
        next_year = clean_number(info.get("epsNextYear"))
        growth = clean_number(info.get("earningsGrowth"))
    if current not in (None, 0) and next_year is not None:
        return percent_change(next_year, current)
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def target_upside(info: dict[str, Any], analyst_targets: dict[str, Any]) -> float | None:
    price = clean_number(info.get("currentPrice") or info.get("regularMarketPrice"))
    target = clean_number(
        analyst_targets.get("mean")
        or analyst_targets.get("targetMeanPrice")
        or info.get("targetMeanPrice")
        or info.get("targetMedianPrice")
    )
    if price in (None, 0) or target is None:
        return None
    return percent_change(target, price)


def current_price_to_cfo(info: dict[str, Any], cashflow: pd.DataFrame) -> float | None:
    market_cap = clean_number(info.get("marketCap"))
    cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    if market_cap is None or cfo in (None, 0):
        return None
    return market_cap / cfo


def ratio_vs_history(
    current_ratio: Any,
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    years: int,
) -> float | None:
    current = clean_number(current_ratio)
    if current is None:
        return None
    historical = approximate_historical_ratio(ratio_name, history, income, balance, cashflow, years)
    if historical is None or historical == 0:
        return None
    return (current - historical) / abs(historical) * 100


def approximate_historical_ratio(
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    years: int,
) -> float | None:
    if history.empty or "Close" not in history:
        return None
    annual_prices = pd.to_numeric(history["Close"], errors="coerce").dropna().resample("YE").median().tail(years)
    if annual_prices.empty:
        return None

    shares_series = row_values(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"])
    revenue_series = row_values(income, ["Total Revenue", "Operating Revenue"])
    net_income_series = row_values(income, ["Net Income", "Net Income Common Stockholders"])
    ebitda_series = row_values(income, ["EBITDA", "Normalized EBITDA"])
    cfo_series = row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    debt_series = row_values(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
    cash_series = row_values(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])

    ratios: list[float] = []
    for date, price in annual_prices.items():
        shares = value_on_or_before(shares_series, date)
        if shares in (None, 0):
            continue
        market_cap = price * shares
        if ratio_name == "ps":
            denominator = value_on_or_before(revenue_series, date)
        elif ratio_name == "pe":
            denominator = value_on_or_before(net_income_series, date)
        elif ratio_name == "ev_ebitda":
            ebitda = value_on_or_before(ebitda_series, date)
            debt = value_on_or_before(debt_series, date) or 0
            cash = value_on_or_before(cash_series, date) or 0
            denominator = ebitda
            market_cap = market_cap + debt - cash
        else:
            denominator = value_on_or_before(cfo_series, date)
        if denominator not in (None, 0):
            ratio = clean_number(market_cap / denominator)
            if ratio and ratio > 0:
                ratios.append(ratio)
    if not ratios:
        return None
    return float(np.median(ratios))


def value_on_or_before(values: pd.Series, date: Any) -> float | None:
    if values.empty:
        return None
    series = pd.to_numeric(values, errors="coerce").dropna()
    if series.empty:
        return None
    try:
        index = pd.to_datetime(series.index)
        target = pd.Timestamp(date)
        dated = pd.Series(series.to_numpy(), index=index).sort_index()
        eligible = dated[dated.index <= target]
        if eligible.empty:
            eligible = dated
        return clean_number(eligible.iloc[-1])
    except Exception:
        return clean_number(series.iloc[-1])


def build_charts_data(
    income: pd.DataFrame,
    cashflow: pd.DataFrame,
    balance: pd.DataFrame,
    history: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    financials = pd.DataFrame(
        {
            "Revenue": row_values(income, ["Total Revenue", "Operating Revenue"]),
            "Net Income": row_values(income, ["Net Income", "Net Income Common Stockholders"]),
            "Operating Cash Flow": row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
        }
    ).dropna(how="all")
    fundamentals = pd.DataFrame(
        {
            "Total Assets": row_values(balance, ["Total Assets"]),
            "Total Debt": row_values(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"]),
        }
    ).dropna(how="all")
    prices = pd.DataFrame()
    if not history.empty and "Close" in history:
        prices = history[["Close"]].dropna()
    return {
        "financials": financials,
        "fundamentals": fundamentals,
        "prices": prices,
    }
