from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ticker_analyzer.analysis.valuation_basis import reporting_market_cap
from ticker_analyzer.metrics.estimates import (  # noqa: F401
    estimate_growth,
    estimate_growth_from_table,
    estimate_growth_note,
    estimate_pair,
    estimate_pair_has_non_positive_value,
    estimate_row,
    growth_from_estimates,
    target_upside,
)
from ticker_analyzer.metrics.periods import ValuationPeriods, valuation_periods
from ticker_analyzer.metrics.utils import (
    clean_number,
    latest_row_value,
    median_or_none,
    metric_value,
    range_median_note,
    value_on_or_before,
)


@dataclass
class HistoricalRatioContext:
    history: pd.DataFrame
    income: pd.DataFrame
    balance: pd.DataFrame
    cashflow: pd.DataFrame
    years: int
    quarterly_income: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarterly_balance: pd.DataFrame = field(default_factory=pd.DataFrame)
    quarterly_cashflow: pd.DataFrame = field(default_factory=pd.DataFrame)
    periods: dict[str, ValuationPeriods] = field(init=False)
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
        definitions = {
            "shares": (self.balance, self.quarterly_balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"], True),
            "revenue": (self.income, self.quarterly_income, ["Total Revenue", "Operating Revenue"], False),
            "net_income": (self.income, self.quarterly_income, ["Net Income Common Stockholders", "Net Income"], False),
            "ebitda": (self.income, self.quarterly_income, ["EBITDA", "Normalized EBITDA"], False),
            "cfo": (self.cashflow, self.quarterly_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], False),
            "equity": (self.balance, self.quarterly_balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"], True),
            "debt": (self.balance, self.quarterly_balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"], True),
            "cash": (self.balance, self.quarterly_balance, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"], True),
        }
        self.periods = {}
        for name, (annual, quarterly, rows, stock) in definitions.items():
            self.periods[name] = valuation_periods(annual, quarterly, rows, stock=stock)
            setattr(self, name, self.periods[name].historical())

    def current_denominator(self, ratio_name: str) -> tuple[float | None, str]:
        name = {"ps": "revenue", "pe": "net_income", "ev_ebitda": "ebitda", "pb": "equity"}.get(ratio_name, "cfo")
        return self.periods[name].current()

    @property
    def latest_shares(self) -> float | None:
        if self.shares.empty:
            return None
        return clean_number(self.shares.iloc[-1])

    def statement_aligned_current_ratio(self, ratio_name: str, info: dict[str, Any]) -> float | None:
        market_cap = reporting_market_cap(info)
        if market_cap is None and not info.get("valuationBasisPrepared"):
            current_price = clean_number(info.get("currentPrice") or info.get("regularMarketPrice"))
            shares = self.latest_shares
            if current_price is not None and shares not in (None, 0):
                market_cap = current_price * shares
        if market_cap is None:
            return None

        denominator, _ = self.current_denominator(ratio_name)
        if ratio_name == "ev_ebitda":
            market_cap = statement_aligned_enterprise_value(market_cap, self)
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
    quarterly_income: pd.DataFrame | None = None,
    quarterly_balance: pd.DataFrame | None = None,
    quarterly_cashflow: pd.DataFrame | None = None,
) -> HistoricalRatioContext:
    return HistoricalRatioContext(
        history, income, balance, cashflow, years,
        quarterly_income=quarterly_income if quarterly_income is not None else pd.DataFrame(),
        quarterly_balance=quarterly_balance if quarterly_balance is not None else pd.DataFrame(),
        quarterly_cashflow=quarterly_cashflow if quarterly_cashflow is not None else pd.DataFrame(),
    )


def make_point_in_time(
    series: pd.Series,
    *,
    filing_dates: dict[Any, Any] | None = None,
    default_filing_lag_days: int = 90,
) -> pd.Series:
    """Move period-end facts to the earliest conservative availability date.

    Explicit filing dates should be used by primary-source providers. yfinance
    statements expose period ends only, so a 90-day lag prevents future annual
    facts from leaking into historical valuation observations.
    """
    if series.empty:
        return series
    shifted = series.copy()
    dates = pd.to_datetime(shifted.index, errors="coerce")
    valid = ~dates.isna()
    shifted = shifted.loc[valid]
    availability = []
    for period in dates[valid]:
        explicit = (filing_dates or {}).get(pd.Timestamp(period))
        filed = pd.to_datetime(explicit, errors="coerce")
        availability.append(
            pd.Timestamp(filed) if not pd.isna(filed) else pd.Timestamp(period) + pd.Timedelta(days=default_filing_lag_days)
        )
    shifted.index = pd.DatetimeIndex(availability)
    return shifted.sort_index()


def known_facts_at(
    facts: pd.DataFrame,
    as_of: Any,
    *,
    filed_at_column: str = "filed_at",
    period_end_column: str = "period_end",
) -> pd.DataFrame:
    """Return only facts that had actually been filed by ``as_of``."""
    if facts.empty or filed_at_column not in facts:
        return facts.iloc[0:0].copy()
    cutoff = pd.Timestamp(as_of)
    filing_dates = pd.to_datetime(facts[filed_at_column], errors="coerce")
    result = facts.loc[filing_dates.notna() & (filing_dates <= cutoff)].copy()
    if period_end_column in result:
        result = result.sort_values([period_end_column, filed_at_column])
        result = result.drop_duplicates(period_end_column, keep="last")
    return result


def point_in_time_multiple(
    *,
    market_cap: float,
    facts: pd.DataFrame,
    price_date: Any,
    field: str,
) -> float | None:
    available = known_facts_at(facts, price_date)
    if available.empty or field not in available:
        return None
    denominator = clean_number(available.iloc[-1][field])
    if denominator in (None, 0):
        return None
    multiple = clean_number(market_cap / denominator)
    return multiple if multiple is not None and multiple > 0 else None


def annual_price_series(history: pd.DataFrame, years: int) -> pd.Series:
    if history.empty or "Close" not in history:
        return pd.Series(dtype=float)
    # Monthly point-in-time observations avoid the sampling bias of one annual
    # median while still keeping the historical multiple series compact.
    return (
        pd.to_numeric(history["Close"], errors="coerce")
        .dropna()
        .resample("ME")
        .median()
        .tail(max(1, years) * 12)
    )


def latest_series_value(series: pd.Series) -> float | None:
    if series.empty:
        return None
    return clean_number(series.iloc[-1])


def statement_aligned_enterprise_value(market_cap: float, context: HistoricalRatioContext) -> float:
    debt = context.periods["debt"].current()[0] or 0
    cash = context.periods["cash"].current()[0] or 0
    return market_cap + debt - cash


def current_price_to_cfo(info: dict[str, Any], cashflow: pd.DataFrame) -> float | None:
    market_cap = reporting_market_cap(info)
    cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    if market_cap is None or cfo in (None, 0):
        return None
    return market_cap / cfo


def fcf_yield(info: dict[str, Any], cashflow: pd.DataFrame) -> float | None:
    market_cap = reporting_market_cap(info)
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
    market_cap = reporting_market_cap(info)
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
    current, source = current_valuation_multiple(
        info,
        ratio_name,
        context,
        fallback_current_ratio=fallback_current_ratio,
    )
    ratios = context.historical_ratios(ratio_name)
    minimum = 1 if context.years == 1 else 2
    note = f"Selected {context.years}Y range; {len(ratios)} monthly valuation observations; historical TTM where available, annual fallback otherwise"
    if prefix:
        note = f"{prefix}; {note}"
    note = f"{note}; {source}"
    if current is None:
        return metric_value(None, f"{note}; current ratio unavailable")
    if len(ratios) < minimum:
        return metric_value(None, f"{note}; requires at least {minimum} observation(s)")
    historical = median_or_none(ratios)
    if historical in (None, 0):
        return metric_value(None, f"{note}; historical median unavailable")
    comparison = f"current {current:.2f}x vs selected-range median {historical:.2f}x"
    return metric_value((current - historical) / abs(historical) * 100, f"{note}; {comparison}")


def current_valuation_multiple(
    info: dict[str, Any],
    ratio_name: str,
    context: HistoricalRatioContext,
    *,
    fallback_current_ratio: Any = None,
) -> tuple[float | None, str]:
    current = context.statement_aligned_current_ratio(ratio_name, info)
    denominator, period = context.current_denominator(ratio_name)
    source = f"statement-aligned current multiple; {period}"
    if denominator is not None and denominator <= 0:
        return None, f"{source}; non-positive denominator"
    if current is None:
        current = clean_number(fallback_current_ratio)
        source = "yfinance current multiple fallback; provider period (statement reconstruction unavailable)"
    elif clean_number(fallback_current_ratio) is not None:
        source += f"; provider-reported multiple {float(fallback_current_ratio):.2f}x"
    if current is None or current <= 0:
        return None, source
    return current, source


def current_absolute_multiple(reported: Any, statement_aligned: Any) -> tuple[float | None, str]:
    current = clean_number(reported)
    if current is not None and current > 0:
        return current, "yfinance reported current multiple"
    current = clean_number(statement_aligned)
    if current is not None and current > 0:
        return current, "statement-aligned current multiple fallback"
    return None, "positive current multiple unavailable"
