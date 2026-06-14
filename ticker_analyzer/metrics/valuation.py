from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.metrics.formulas import free_cash_flow_series
from ticker_analyzer.metrics.utils import *


def estimate_growth(
    info: dict[str, Any],
    kind: str,
    estimate_table: pd.DataFrame | None = None,
    growth_estimates: pd.DataFrame | None = None,
    *,
    min_analysts: int = 5,
) -> float | None:
    structured_growth = estimate_growth_from_table(estimate_table, min_analysts=min_analysts)
    if structured_growth is not None:
        return structured_growth
    growth_estimate = growth_from_estimates(growth_estimates, period="+1y")
    if growth_estimate is not None:
        return growth_estimate
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


def estimate_growth_from_table(table: pd.DataFrame | None, *, min_analysts: int) -> float | None:
    if table is None or table.empty:
        return None
    current = estimate_row(table, "0y")
    next_year = estimate_row(table, "+1y")
    if current is None or next_year is None:
        return None
    analysts = clean_number(next_year.get("numberOfAnalysts"))
    if analysts is not None and analysts < min_analysts:
        return None
    current_avg = clean_number(current.get("avg"))
    next_avg = clean_number(next_year.get("avg"))
    if current_avg not in (None, 0) and next_avg is not None:
        return percent_change(next_avg, current_avg)
    growth = clean_number(next_year.get("growth"))
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def estimate_row(table: pd.DataFrame, period: str) -> dict[str, Any] | None:
    if period not in table.index:
        return None
    row = table.loc[period]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return row.to_dict()


def growth_from_estimates(table: pd.DataFrame | None, *, period: str) -> float | None:
    if table is None or table.empty or period not in table.index:
        return None
    row = table.loc[period]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    growth = clean_number(row.get("stockTrend"))
    if growth is None:
        return None
    return growth * 100 if abs(growth) < 2 else growth


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


def fcf_yield(info: dict[str, Any], cashflow: pd.DataFrame) -> float | None:
    market_cap = clean_number(info.get("marketCap"))
    free_cash_flow = latest_row_value(cashflow, ["Free Cash Flow"])
    if free_cash_flow is None:
        cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex = latest_row_value(cashflow, ["Capital Expenditure", "Capital Expenditures"])
        if cfo is not None and capex is not None:
            free_cash_flow = cfo + capex if capex < 0 else cfo - capex
    if market_cap in (None, 0) or free_cash_flow is None:
        return None
    return free_cash_flow / market_cap * 100


def current_price_to_book(info: dict[str, Any], balance: pd.DataFrame) -> float | None:
    reported = clean_number(info.get("priceToBook"))
    if reported is not None:
        return reported
    market_cap = clean_number(info.get("marketCap"))
    equity = latest_row_value(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
    if market_cap is None or equity in (None, 0):
        return None
    return market_cap / equity


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


def ratio_vs_history_metric(
    current_ratio: Any,
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    years: int,
    prefix: str = "",
) -> dict[str, Any]:
    current = clean_number(current_ratio)
    ratios = approximate_historical_ratios(ratio_name, history, income, balance, cashflow, years)
    minimum = 1 if years == 1 else 2
    note = range_median_note(years, len(ratios), prefix)
    if current is None:
        return metric_value(None, f"{note}; current ratio unavailable")
    if len(ratios) < minimum:
        return metric_value(None, f"{note}; requires at least {minimum} observation(s)")
    historical = median_or_none(ratios)
    if historical in (None, 0):
        return metric_value(None, f"{note}; historical median unavailable")
    return metric_value((current - historical) / abs(historical) * 100, note)


def approximate_historical_ratio(
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    years: int,
) -> float | None:
    return median_or_none(approximate_historical_ratios(ratio_name, history, income, balance, cashflow, years))


def approximate_historical_ratios(
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    years: int,
) -> list[float]:
    if history.empty or "Close" not in history:
        return []
    annual_prices = pd.to_numeric(history["Close"], errors="coerce").dropna().resample("YE").median().tail(years)
    if annual_prices.empty:
        return []

    shares_series = row_values(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"])
    revenue_series = row_values(income, ["Total Revenue", "Operating Revenue"])
    net_income_series = row_values(income, ["Net Income", "Net Income Common Stockholders"])
    ebitda_series = row_values(income, ["EBITDA", "Normalized EBITDA"])
    cfo_series = row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    equity_series = row_values(balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
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
        elif ratio_name == "pb":
            denominator = value_on_or_before(equity_series, date)
        else:
            denominator = value_on_or_before(cfo_series, date)
        if denominator not in (None, 0):
            ratio = clean_number(market_cap / denominator)
            if ratio and ratio > 0:
                ratios.append(ratio)
    return ratios
