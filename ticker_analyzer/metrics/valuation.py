from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from ticker_analyzer.metrics.utils import (
    clean_number,
    latest_row_value,
    median_or_none,
    metric_value,
    percent_change,
    range_median_note,
    row_values,
    value_on_or_before,
)


@dataclass
class HistoricalRatioContext:
    history: pd.DataFrame
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    years: int
    annual_prices: pd.Series = field(init=False)
    shares: pd.Series = field(init=False)
    revenue: pd.Series = field(init=False)
    net_income: pd.Series = field(init=False)
    ebitda: pd.Series = field(init=False)
    cfo: pd.Series = field(init=False)
    equity: pd.Series = field(init=False)
    debt: pd.Series = field(init=False)
    cash: pd.Series = field(init=False)

    def __post_init__(self) -> None:
        self.annual_prices = annual_price_series(self.history, self.years)
        self.shares = row_values(
            self.balance,
            ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"],
        )
        self.revenue = row_values(self.income, ["Total Revenue", "Operating Revenue"])
        self.net_income = row_values(self.income, ["Net Income", "Net Income Common Stockholders"])
        self.ebitda = row_values(self.income, ["EBITDA", "Normalized EBITDA"])
        self.cfo = row_values(self.cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        self.equity = row_values(self.balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"])
        self.debt = row_values(self.balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
        self.cash = row_values(
            self.balance,
            ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
        )

    @property
    def latest_shares(self) -> float | None:
        if self.shares.empty:
            return None
        return clean_number(self.shares.iloc[-1])

    def statement_aligned_current_ratio(self, ratio_name: str, info: dict[str, Any]) -> float | None:
        market_cap = clean_number(info.get("marketCap"))
        if market_cap is None:
            current_price = clean_number(info.get("currentPrice") or info.get("regularMarketPrice"))
            shares = self.latest_shares
            if current_price is not None and shares not in (None, 0):
                market_cap = current_price * shares
        if market_cap is None:
            return None

        denominator: float | None
        if ratio_name == "ps":
            denominator = latest_series_value(self.revenue)
        elif ratio_name == "pe":
            denominator = latest_series_value(self.net_income)
        elif ratio_name == "ev_ebitda":
            denominator = latest_series_value(self.ebitda)
            market_cap = statement_aligned_enterprise_value(market_cap, self)
        elif ratio_name == "pb":
            denominator = latest_series_value(self.equity)
        else:
            denominator = latest_series_value(self.cfo)
        if denominator in (None, 0):
            return None
        ratio = clean_number(market_cap / denominator)
        return ratio if ratio is not None and ratio > 0 else None

    def historical_ratios(self, ratio_name: str) -> list[float]:
        ratios: list[float] = []
        for date, price in self.annual_prices.items():
            shares = value_on_or_before(self.shares, date)
            if shares in (None, 0):
                continue
            market_cap = price * shares
            denominator = self._historical_denominator(ratio_name, date)
            if ratio_name == "ev_ebitda":
                debt = value_on_or_before(self.debt, date) or 0
                cash = value_on_or_before(self.cash, date) or 0
                market_cap = market_cap + debt - cash
            if denominator not in (None, 0):
                ratio = clean_number(market_cap / denominator)
                if ratio is not None and ratio > 0:
                    ratios.append(ratio)
        return ratios

    def _historical_denominator(self, ratio_name: str, date: Any) -> float | None:
        if ratio_name == "ps":
            return value_on_or_before(self.revenue, date)
        if ratio_name == "pe":
            return value_on_or_before(self.net_income, date)
        if ratio_name == "ev_ebitda":
            return value_on_or_before(self.ebitda, date)
        if ratio_name == "pb":
            return value_on_or_before(self.equity, date)
        return value_on_or_before(self.cfo, date)


def build_historical_ratio_context(
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    years: int,
) -> HistoricalRatioContext:
    return HistoricalRatioContext(history, income, balance, cashflow, years)


def annual_price_series(history: pd.DataFrame, years: int) -> pd.Series:
    if history.empty or "Close" not in history:
        return pd.Series(dtype=float)
    return pd.to_numeric(history["Close"], errors="coerce").dropna().resample("YE").median().tail(years)


def latest_series_value(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return clean_number(series.iloc[-1])


def statement_aligned_enterprise_value(market_cap: float, context: HistoricalRatioContext) -> float:
    debt = latest_series_value(context.debt) or 0
    cash = latest_series_value(context.cash) or 0
    return market_cap + debt - cash


def estimate_growth(
    info: dict[str, Any],
    kind: str,
    estimate_table: pd.DataFrame | None = None,
    growth_estimates: pd.DataFrame | None = None,
    *,
    min_analysts: int = 5,
) -> float | None:
    positive_only = kind == "eps"
    structured_growth = estimate_growth_from_table(
        estimate_table,
        min_analysts=min_analysts,
        positive_only=positive_only,
    )
    if structured_growth is not None:
        return structured_growth
    if positive_only and estimate_pair_has_non_positive_value(estimate_table, min_analysts=min_analysts):
        return None
    if positive_only:
        info_current = clean_number(info.get("epsCurrentYear"))
        info_next_year = clean_number(info.get("epsNextYear"))
        if (
            info_current is not None
            and info_next_year is not None
            and (info_current <= 0 or info_next_year <= 0)
        ):
            return None
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
    if positive_only and current is not None and next_year is not None and (current <= 0 or next_year <= 0):
        return None
    if current not in (None, 0) and next_year is not None:
        return percent_change(next_year, current)
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def estimate_growth_from_table(
    table: pd.DataFrame | None,
    *,
    min_analysts: int,
    positive_only: bool = False,
) -> float | None:
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
    if positive_only and current_avg is not None and next_avg is not None and (current_avg <= 0 or next_avg <= 0):
        return None
    if current_avg not in (None, 0) and next_avg is not None:
        return percent_change(next_avg, current_avg)
    growth = clean_number(next_year.get("growth"))
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def estimate_growth_note(
    kind: str,
    estimate_table: pd.DataFrame | None,
    info: dict[str, Any] | None = None,
    *,
    min_analysts: int = 5,
) -> str:
    if kind == "eps" and estimate_pair_has_non_positive_value(estimate_table, min_analysts=min_analysts):
        current, next_year = estimate_pair(estimate_table)
        if current is not None and next_year is not None and current <= 0 < next_year:
            return "EPS turnaround: estimates move from non-positive to positive; excluded from percentage-growth scoring"
        return "EPS estimate growth excluded because current or next-year EPS is non-positive"
    if kind == "eps" and info:
        current = clean_number(info.get("epsCurrentYear"))
        next_year = clean_number(info.get("epsNextYear"))
        if current is not None and next_year is not None and current <= 0 < next_year:
            return "EPS turnaround: estimates move from non-positive to positive; excluded from percentage-growth scoring"
        if current is not None and next_year is not None and (current <= 0 or next_year <= 0):
            return "EPS estimate growth excluded because current or next-year EPS is non-positive"
    source = "earnings" if kind == "eps" else "revenue"
    return f"Uses structured yfinance {source}_estimate when enough analysts are available, then falls back to other yfinance estimate fields"


def estimate_pair_has_non_positive_value(table: pd.DataFrame | None, *, min_analysts: int) -> bool:
    if table is None or table.empty:
        return False
    next_year = estimate_row(table, "+1y")
    if next_year is None:
        return False
    analysts = clean_number(next_year.get("numberOfAnalysts"))
    if analysts is not None and analysts < min_analysts:
        return False
    current, future = estimate_pair(table)
    return current is not None and future is not None and (current <= 0 or future <= 0)


def estimate_pair(table: pd.DataFrame | None) -> tuple[float | None, float | None]:
    if table is None or table.empty:
        return None, None
    current = estimate_row(table, "0y")
    next_year = estimate_row(table, "+1y")
    if current is None or next_year is None:
        return None, None
    return clean_number(current.get("avg")), clean_number(next_year.get("avg"))


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
    context = build_historical_ratio_context(history, income, balance, cashflow, years=years)
    current = clean_number(current_ratio)
    if current is None:
        return None
    historical = median_or_none(context.historical_ratios(ratio_name))
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
    context = build_historical_ratio_context(history, income, balance, cashflow, years=years)
    ratios = context.historical_ratios(ratio_name)
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
    context = build_historical_ratio_context(history, income, balance, cashflow, years=years)
    return median_or_none(context.historical_ratios(ratio_name))


def approximate_historical_ratios(
    ratio_name: str,
    history: pd.DataFrame,
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    years: int,
) -> list[float]:
    context = build_historical_ratio_context(history, income, balance, cashflow, years=years)
    return context.historical_ratios(ratio_name)


def statement_aligned_ratio_vs_history_metric(
    info: dict[str, Any],
    ratio_name: str,
    context: HistoricalRatioContext,
    *,
    fallback_current_ratio: Any = None,
    prefix: str = "",
) -> dict[str, Any]:
    current = context.statement_aligned_current_ratio(ratio_name, info)
    source = "statement-aligned current multiple"
    if current is None:
        current = clean_number(fallback_current_ratio)
        source = "yfinance current multiple fallback"
    ratios = context.historical_ratios(ratio_name)
    minimum = 1 if context.years == 1 else 2
    note = range_median_note(context.years, len(ratios), prefix)
    note = f"{note}; {source}"
    if current is None:
        return metric_value(None, f"{note}; current ratio unavailable")
    if len(ratios) < minimum:
        return metric_value(None, f"{note}; requires at least {minimum} observation(s)")
    historical = median_or_none(ratios)
    if historical in (None, 0):
        return metric_value(None, f"{note}; historical median unavailable")
    return metric_value((current - historical) / abs(historical) * 100, note)
