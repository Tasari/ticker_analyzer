from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


CONFIG_PATH = Path("metrics_config.json")


@dataclass
class MetricResult:
    id: str
    name: str
    value: float | None
    unit: str
    score: float | None
    weight: float
    status: str
    note: str = ""
    description: str = ""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def analyze_ticker(ticker_symbol: str, ranges: str | dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    ticker_symbol = ticker_symbol.strip().upper()
    if not ticker_symbol:
        raise ValueError("Enter a ticker symbol.")
    selected_ranges = normalize_ranges(ranges)
    growth_range = selected_ranges["Growth"]
    value_range = selected_ranges["Value"]

    ticker = yf.Ticker(ticker_symbol)
    info = safe_dict(lambda: ticker.info)
    if not info and ticker_symbol:
        raise ValueError(f"No data returned for {ticker_symbol}.")

    annual_income = normalize_statement(safe_frame(lambda: ticker.financials))
    annual_balance = normalize_statement(safe_frame(lambda: ticker.balance_sheet))
    annual_cashflow = normalize_statement(safe_frame(lambda: ticker.cashflow))
    quarterly_income = normalize_statement(safe_frame(lambda: ticker.quarterly_financials))
    quarterly_balance = normalize_statement(safe_frame(lambda: ticker.quarterly_balance_sheet))
    growth_history = safe_frame(lambda: ticker.history(period=growth_range.lower(), auto_adjust=False))
    value_history = safe_frame(lambda: ticker.history(period=value_range.lower(), auto_adjust=False))
    earnings_dates = safe_frame(lambda: ticker.get_earnings_dates(limit=16))
    analyst_targets = safe_dict(lambda: ticker.analyst_price_targets)
    if is_empty_ticker_response(info, annual_income, annual_balance, annual_cashflow, growth_history):
        raise ValueError(f"No usable data returned for {ticker_symbol}. Check the ticker symbol and try again.")
    range_years = {
        tab_name: years_from_range(tab_range)
        for tab_name, tab_range in selected_ranges.items()
    }

    raw_metrics = build_raw_metrics(
        info=info,
        annual_income=annual_income,
        annual_balance=annual_balance,
        annual_cashflow=annual_cashflow,
        quarterly_income=quarterly_income,
        quarterly_balance=quarterly_balance,
        growth_history=growth_history,
        value_history=value_history,
        earnings_dates=earnings_dates,
        analyst_targets=analyst_targets,
        range_years=range_years,
    )

    tab_results: dict[str, Any] = {}
    missing: list[str] = []
    for tab_name, metric_configs in config.get("metrics", {}).items():
        configured_raw_metrics = apply_configured_metric_fallbacks(raw_metrics, metric_configs)
        metric_results = [score_metric(metric_config, configured_raw_metrics, tab_name, config) for metric_config in metric_configs]
        tab_score = weighted_score(metric_results)
        tab_results[tab_name] = {
            "score": tab_score,
            "rating": classify_tab_rating(tab_name, tab_score, config),
            "metrics": metric_results,
        }
        missing.extend(
            f"{tab_name}: {metric.name} ({metric.note or 'data unavailable'})"
            for metric in metric_results
            if metric.score is None
        )

    overall_score = weighted_tab_score(tab_results, config.get("tab_weights", {}))
    rating = classify_rating(overall_score, config)

    return {
        "ticker": ticker_symbol,
        "company_name": info.get("longName") or info.get("shortName") or ticker_symbol,
        "currency": info.get("currency", ""),
        "current_price": clean_number(info.get("currentPrice") or info.get("regularMarketPrice")),
        "overall_score": overall_score,
        "rating": rating,
        "tabs": tab_results,
        "missing": missing,
        "raw": raw_metrics,
        "ranges": selected_ranges,
        "charts": build_charts_data(annual_income, annual_cashflow, annual_balance, growth_history),
    }


def safe_frame(callback) -> pd.DataFrame:
    try:
        value = callback()
    except Exception:
        return pd.DataFrame()
    if value is None:
        return pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def safe_dict(callback) -> dict[str, Any]:
    try:
        value = callback()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def years_from_range(price_range: str) -> int:
    normalized = price_range.strip().lower()
    if normalized.endswith("y"):
        try:
            return max(1, int(normalized[:-1]))
        except ValueError:
            return 3
    return 3


def normalize_ranges(ranges: str | dict[str, str]) -> dict[str, str]:
    if isinstance(ranges, str):
        return {
            "Growth": ranges,
            "Fundamentals": ranges,
            "Value": ranges,
        }
    default = "3Y"
    return {
        "Growth": ranges.get("Growth", default),
        "Fundamentals": ranges.get("Fundamentals", default),
        "Value": ranges.get("Value", default),
    }


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


def normalize_statement(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    try:
        frame.columns = pd.to_datetime(frame.columns)
        frame = frame.sort_index(axis=1)
    except Exception:
        pass
    return frame


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
    price_change = percentage_change_from_history(growth_history)

    revenue_estimate_growth = estimate_growth(info, "revenue")
    eps_estimate_growth = estimate_growth(info, "eps")
    price_target_upside = target_upside(info, analyst_targets)

    raw = {
        "revenue_ttm_range_growth": metric_value(percent_change(revenue_ttm, revenue_range_base), f"Revenue TTM compared with annual revenue from {growth_years} fiscal year(s) ago"),
        "revenue_ttm_growth": metric_value(percent_change(revenue_ttm, revenue_prior_ttm), "TTM vs previous TTM"),
        "net_income_range_growth": metric_value(percent_change(latest_row_value(annual_income, ["Net Income", "Net Income Common Stockholders"]), net_income_range_base), f"Latest annual net income compared with {growth_years} fiscal year(s) ago"),
        "cfo_range_growth": metric_value(percent_change(latest_row_value(annual_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]), cfo_range_base), f"Latest annual operating cash flow compared with {growth_years} fiscal year(s) ago"),
        "operating_margin": metric_value(operating_margin(annual_income)),
        "price_change": metric_value(price_change),
        "revenue_estimate_growth": metric_value(revenue_estimate_growth, "Uses available yfinance analyst estimate fields"),
        "revenue_estimate_avg_growth": metric_value(revenue_estimate_growth, "Proxy from available yfinance estimate fields"),
        "eps_estimate_avg_growth": metric_value(eps_estimate_growth, "Proxy from available yfinance estimate fields"),
        "debt_to_assets": metric_value(debt_to_assets(annual_balance), f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "quick_ratio": metric_value(quick_ratio(info, quarterly_balance, annual_balance), "Uses yfinance quickRatio, then balance sheet fallback"),
        "cfo_to_debt": metric_value(cfo_to_debt(annual_cashflow, annual_balance), f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "interest_coverage": metric_value(interest_coverage(annual_income), f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "ohlson_probability": metric_value(ohlson_probability(annual_income, annual_balance), f"Latest annual value; Fundamentals range is {fundamentals_years}Y"),
        "ps_vs_3y_median": metric_value(ratio_vs_history(info.get("priceToSalesTrailing12Months"), "ps", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), f"Compared with approximate {value_years}Y median"),
        "pe_vs_3y_median": metric_value(ratio_vs_history(info.get("trailingPE"), "pe", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), f"Compared with approximate {value_years}Y median"),
        "ev_ebitda_vs_5y_median": metric_value(ratio_vs_history(info.get("enterpriseToEbitda"), "ev_ebitda", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), f"Compared with approximate {value_years}Y median"),
        "price_to_cfo_vs_5y_median": metric_value(ratio_vs_history(current_price_to_cfo(info, annual_cashflow), "price_to_cfo", value_history, annual_income, annual_balance, annual_cashflow, years=value_years), f"Compared with approximate {value_years}Y median"),
        "price_target": metric_value(price_target_upside),
        "upside_vs_3y_median": metric_value(None, "Uses configured benchmark when historical analyst upside is unavailable"),
    }
    return raw


def apply_configured_metric_fallbacks(
    raw_metrics: dict[str, dict[str, Any]],
    metric_configs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    updated = dict(raw_metrics)
    config_by_id = {metric_config.get("id"): metric_config for metric_config in metric_configs}
    upside_config = config_by_id.get("upside_vs_3y_median")
    price_target_upside = clean_number(updated.get("price_target", {}).get("value"))
    benchmark = clean_number(upside_config.get("benchmark")) if upside_config else None
    if price_target_upside is not None and benchmark is not None:
        updated["upside_vs_3y_median"] = metric_value(
            price_target_upside - benchmark,
            f"Current price target upside minus configured 3Y median benchmark ({benchmark:.2f}%)",
        )
    return updated


def metric_value(value: float | None, note: str = "") -> dict[str, Any]:
    return {"value": clean_number(value), "note": note}


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


def percentage_change_from_history(history: pd.DataFrame) -> float | None:
    if history.empty or "Close" not in history:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2 or close.iloc[0] == 0:
        return None
    return percent_change(close.iloc[-1], close.iloc[0])


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
    if cfo is None or debt in (None, 0):
        return None
    return cfo / debt


def interest_coverage(income: pd.DataFrame) -> float | None:
    operating_income = latest_row_value(income, ["Operating Income", "EBIT"])
    interest = latest_row_value(income, ["Interest Expense", "Interest Expense Non Operating"])
    if operating_income is None or interest in (None, 0):
        return None
    return operating_income / abs(interest)


def ohlson_probability(income: pd.DataFrame, balance: pd.DataFrame) -> float | None:
    assets = latest_row_value(balance, ["Total Assets"])
    liabilities = latest_row_value(balance, ["Total Liabilities Net Minority Interest", "Total Liab"])
    current_assets = latest_row_value(balance, ["Current Assets", "Total Current Assets"])
    current_liabilities = latest_row_value(balance, ["Current Liabilities", "Total Current Liabilities"])
    net_income = latest_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    prior_net_income = prior_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    working_capital = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities
    required = [assets, liabilities, current_assets, current_liabilities, net_income, prior_net_income, working_capital]
    if any(value is None for value in required) or assets == 0:
        return None
    size = math.log(max(assets, 1))
    tlta = liabilities / assets
    wcta = working_capital / assets
    clca = current_liabilities / current_assets if current_assets else None
    nita = net_income / assets
    futl = net_income / liabilities if liabilities else None
    intwo = 1 if net_income < 0 else 0
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
    shares = latest_row_value(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"])
    if shares in (None, 0):
        return None

    ratios: list[float] = []
    for price in annual_prices:
        market_cap = price * shares
        if ratio_name == "ps":
            denominator = latest_row_value(income, ["Total Revenue", "Operating Revenue"])
        elif ratio_name == "pe":
            denominator = latest_row_value(income, ["Net Income", "Net Income Common Stockholders"])
        elif ratio_name == "ev_ebitda":
            ebitda = latest_row_value(income, ["EBITDA", "Normalized EBITDA"])
            debt = latest_row_value(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"]) or 0
            cash = latest_row_value(balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]) or 0
            denominator = ebitda
            market_cap = market_cap + debt - cash
        else:
            denominator = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        if denominator not in (None, 0):
            ratio = clean_number(market_cap / denominator)
            if ratio and ratio > 0:
                ratios.append(ratio)
    if not ratios:
        return None
    return float(np.median(ratios))


def score_metric(
    metric_config: dict[str, Any],
    raw_metrics: dict[str, dict[str, Any]],
    tab_name: str,
    config: dict[str, Any],
) -> MetricResult:
    metric_id = metric_config["id"]
    raw = raw_metrics.get(metric_id, {})
    value = clean_number(raw.get("value"))
    if value is None:
        return MetricResult(
            id=metric_id,
            name=metric_config.get("name", metric_id),
            value=None,
            unit=metric_config.get("unit", ""),
            score=None,
            weight=clean_number(metric_config.get("weight")) or 0,
            status="Missing",
            note=raw.get("note", "data unavailable"),
            description=metric_description(metric_config),
        )
    score = score_value(value, metric_config)
    return MetricResult(
        id=metric_id,
        name=metric_config.get("name", metric_id),
        value=value,
        unit=metric_config.get("unit", ""),
        score=score,
        weight=clean_number(metric_config.get("weight")) or 0,
        status=status_from_score(score, tab_name, config),
        note=raw.get("note", ""),
        description=metric_description(metric_config),
    )


def metric_description(metric_config: dict[str, Any]) -> str:
    description = metric_config.get("description", "")
    direction = metric_config.get("direction", "higher")
    good = metric_config.get("good")
    warn = metric_config.get("warn")
    unit = metric_config.get("unit", "")
    weight = clean_number(metric_config.get("weight")) or 0
    direction_text = "higher values improve the score" if direction == "higher" else "lower values improve the score"
    scoring = (
        f"Scoring: {direction_text}. Good threshold: {format_threshold(good, unit)}. "
        f"Weak threshold: {format_threshold(warn, unit)}. Weight: {weight:g}."
    )
    return f"{description} {scoring}".strip()


def format_threshold(value: Any, unit: str) -> str:
    number = clean_number(value)
    if number is None:
        return "not set"
    if unit == "%":
        return f"{number:g}%"
    if unit == "x":
        return f"{number:g}x"
    if unit == "$B":
        return f"${number:g}B"
    if unit == "pp":
        return f"{number:g} pp"
    return f"{number:g}"


def score_value(value: float, metric_config: dict[str, Any]) -> float:
    good = clean_number(metric_config.get("good"))
    warn = clean_number(metric_config.get("warn"))
    direction = metric_config.get("direction", "higher")
    if good is None or warn is None or good == warn:
        return 50
    if direction == "lower":
        if value <= good:
            return 100
        if value >= warn:
            return 0
        return (warn - value) / (warn - good) * 100
    if value >= good:
        return 100
    if value <= warn:
        return 0
    return (value - warn) / (good - warn) * 100


def status_from_score(score: float, tab_name: str, config: dict[str, Any]) -> str:
    labels = config.get("tab_rating_labels", {}).get(tab_name, {})
    if score >= 80:
        return labels.get("very_strong", labels.get("strong", "Very Good"))
    if score >= 60:
        return labels.get("strong", "Good")
    if score >= 40:
        return labels.get("neutral", "Watch")
    if score >= 20:
        return labels.get("weak", "Weak")
    return labels.get("very_weak", labels.get("weak", "Very Weak"))


def weighted_score(metrics: list[MetricResult]) -> float | None:
    available = [metric for metric in metrics if metric.score is not None and metric.weight > 0]
    total_weight = sum(metric.weight for metric in available)
    if total_weight <= 0:
        return None
    return sum((metric.score or 0) * metric.weight for metric in available) / total_weight


def weighted_tab_score(tab_results: dict[str, Any], tab_weights: dict[str, Any]) -> float | None:
    weighted: list[tuple[float, float]] = []
    for tab_name, result in tab_results.items():
        score = clean_number(result.get("score"))
        weight = clean_number(tab_weights.get(tab_name)) or 0
        if score is not None and weight > 0:
            weighted.append((score, weight))
    total_weight = sum(weight for _, weight in weighted)
    if total_weight <= 0:
        return None
    return sum(score * weight for score, weight in weighted) / total_weight


def classify_rating(score: float | None, config: dict[str, Any]) -> str:
    if score is None:
        return "Not Rated"
    default_labels = {
        "very_strong": "Strong Buy",
        "strong": "Buy",
        "neutral": "Hold",
        "weak": "Sell",
        "very_weak": "Strong Sell",
    }
    labels = {**default_labels, **config.get("overall_rating_labels", {})}
    return classify_five_point_score(score, config.get("rating_thresholds", {}), labels)


def classify_five_point_score(
    score: float,
    thresholds: dict[str, Any],
    labels: dict[str, str],
) -> str:
    very_strong = clean_number(thresholds.get("very_strong")) or 80
    strong = clean_number(thresholds.get("strong")) or 60
    neutral = clean_number(thresholds.get("neutral")) or 40
    weak = clean_number(thresholds.get("weak")) or 20
    if score >= very_strong:
        return labels["very_strong"]
    if score >= strong:
        return labels["strong"]
    if score >= neutral:
        return labels["neutral"]
    if score >= weak:
        return labels["weak"]
    return labels["very_weak"]


def classify_tab_rating(tab_name: str, score: float | None, config: dict[str, Any]) -> str:
    if score is None:
        return "Not Rated"
    labels = config.get("tab_rating_labels", {}).get(tab_name, {})
    if score >= 80:
        return labels.get("very_strong", labels.get("strong", "Very Good"))
    if score >= 60:
        return labels.get("strong", "Good")
    if score >= 40:
        return labels.get("neutral", "Watch")
    if score >= 20:
        return labels.get("weak", "Weak")
    return labels.get("very_weak", labels.get("weak", "Very Weak"))


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


def format_metric_value(value: float | None, unit: str) -> str:
    if value is None:
        return "Missing"
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "$B":
        return f"${value:.2f}B"
    if unit == "pp":
        return f"{value:.2f} pp"
    return f"{value:.2f}"
