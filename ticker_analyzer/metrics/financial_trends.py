"""Quarterly chart history and forecasts overlaid at their observation dates."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.analysis.valuation_basis import reporting_market_cap
from ticker_analyzer.metrics.estimates import estimate_row
from ticker_analyzer.metrics.utils import clean_number, row_values, value_on_or_before
from ticker_analyzer.metrics.valuation import build_historical_ratio_context


def _annual_financial_trends(
    income: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    revenue_estimate: pd.DataFrame | None = None,
    info: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    info = info or {}
    series = {
        "Revenue": row_values(income, ["Total Revenue", "Operating Revenue"]),
        "Net Income": row_values(income, ["Net Income", "Net Income Common Stockholders"]),
        "Operating Cash Flow": row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
    }
    # Anchor the window to the latest reported income year, even if cash flow is older.
    dates = pd.to_datetime(income.columns, errors="coerce").dropna()
    if dates.empty:
        dates = pd.to_datetime(cashflow.columns, errors="coerce").dropna()
    if dates.empty:
        empty = pd.DataFrame(columns=list(series), dtype=float)
        return empty, empty.copy(), pd.DataFrame(columns=list(series))
    latest = dates.max().normalize()
    historical_dates = pd.DatetimeIndex([latest - pd.DateOffset(years=offset) for offset in (2, 1, 0)])
    history = pd.DataFrame(index=historical_dates, columns=list(series), dtype=float)
    for metric, values in series.items():
        values = values.copy()
        values.index = pd.to_datetime(values.index, errors="coerce")
        values = values[values.index.notna()].sort_index()
        for date in historical_dates:
            # Accommodate 52/53-week fiscal years without mixing adjacent years.
            candidates = values[abs(values.index - date) < pd.Timedelta(days=180)]
            if not candidates.empty:
                history.loc[date, metric] = candidates.iloc[-1]

    future_dates = pd.DatetimeIndex([latest + pd.DateOffset(years=year) for year in (1, 2)])
    future = pd.DataFrame(index=future_dates, columns=history.columns, dtype=float)
    sources = pd.DataFrame("Unavailable", index=future_dates, columns=history.columns)
    currency = str(info.get("financialCurrency") or income.attrs.get("financial_currency") or "")
    for frame in (history, future):
        frame.attrs["financial_currency"] = currency

    revenue = history["Revenue"].dropna()
    annual_growth = None
    if len(revenue) >= 2 and revenue.iloc[0] > 0 and revenue.iloc[-1] > 0:
        years = revenue.index[-1].year - revenue.index[0].year
        annual_growth = (revenue.iloc[-1] / revenue.iloc[0]) ** (1 / years) - 1

    # Yahoo's 0y/+1y refer to its current/next fiscal years, not calendar years.
    next_end = clean_number(info.get("nextFiscalYearEnd"))
    estimate_start = pd.to_datetime(next_end, unit="s").normalize() if next_end is not None else future_dates[0]
    for year, date in enumerate(future_dates, start=1):
        consensus = None
        if revenue_estimate is not None and not revenue_estimate.empty:
            for period, offset in (("0y", 0), ("+1y", 1)):
                estimate_date = estimate_start + pd.DateOffset(years=offset)
                if abs(estimate_date - date) >= pd.Timedelta(days=180):
                    continue
                row = estimate_row(revenue_estimate, period)
                if row is None:
                    continue
                estimate_currency = str(row.get("currency") or currency)
                if not currency or estimate_currency != currency:
                    continue
                consensus = clean_number(row.get("avg"))
                if consensus is not None and consensus >= 0:
                    future.loc[date, "Revenue"] = consensus
                    sources.loc[date, "Revenue"] = "Analyst revenue consensus (Yahoo Finance)"
                    break
                consensus = None
        if consensus is None and annual_growth is not None and pd.notna(history.loc[latest, "Revenue"]):
            future.loc[date, "Revenue"] = clean_number(history.loc[latest, "Revenue"] * (1 + annual_growth) ** year)
            sources.loc[date, "Revenue"] = "Model projection: historical revenue CAGR"

    for metric in ("Net Income", "Operating Cash Flow"):
        margins = (history[metric] / history["Revenue"].where(history["Revenue"] > 0)).dropna()
        if margins.empty:
            continue
        future[metric] = (future["Revenue"] * margins.median()).map(clean_number)
        sources.loc[future[metric].notna(), metric] = "Model projection: median historical margin × forecast revenue"
    return history, future, sources


FINANCIAL_ROWS = {
    "Revenue": ["Total Revenue", "Operating Revenue"],
    "Net Income": ["Net Income", "Net Income Common Stockholders"],
    "Operating Cash Flow": ["Operating Cash Flow", "Total Cash From Operating Activities"],
}


def quarterly_values(frame: pd.DataFrame, names: list[str]) -> pd.Series:
    """Keep missing quarters visible and reject semiannual data as quarterly."""
    values = row_values(frame, names)
    if values.empty:
        return pd.Series(index=pd.DatetimeIndex([]), dtype=float)
    values.index = pd.DatetimeIndex(pd.to_datetime(values.index)).tz_localize(None)
    values = values[~values.index.duplicated(keep="last")].sort_index()
    if len(values) > 1:
        gaps = values.index.to_series().diff().dropna().dt.days
        if not gaps.between(60, 120).any():
            return pd.Series(index=pd.DatetimeIndex([]), dtype=float)
    dates = pd.date_range(values.index[0], values.index[-1], freq=pd.DateOffset(months=3))
    aligned = pd.Series(index=dates, dtype=float)
    for date in dates:
        nearby = values[abs(values.index - date) < pd.Timedelta(days=40)]
        if not nearby.empty:
            aligned.loc[date] = nearby.iloc[-1]
    return aligned


def _publication_date(frame: pd.DataFrame, period: pd.Timestamp) -> pd.Timestamp:
    dates = pd.DatetimeIndex(pd.to_datetime(frame.columns)).tz_localize(None)
    nearby = dates[abs(dates - period) < pd.Timedelta(days=40)]
    original = nearby[-1] if len(nearby) else period
    filed = pd.to_datetime(frame.attrs.get("filed_dates", {}).get(original), errors="coerce")
    return pd.Timestamp(filed).tz_localize(None) if pd.notna(filed) else original + pd.Timedelta(days=90)


def _known_quarters(frame: pd.DataFrame, values: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    return values.loc[[_publication_date(frame, period) <= as_of for period in values.index]]


def projected_revenue(periods: pd.DataFrame, as_of: pd.Timestamp) -> float | None:
    """Project annual/TTM revenue without using subsequently published facts."""
    if periods.empty:
        return None
    known = periods[periods["available"] <= as_of].sort_values(["period", "priority"])
    known = known.drop_duplicates("period", keep="last")
    if len(known) < 2:
        return None
    latest = known.iloc[-1]
    # Use a comparable annual or TTM observation at least a year earlier.
    earlier = known[
        (known["period"] <= latest.period - pd.Timedelta(days=330))
        & (known["period"] >= latest.period - pd.Timedelta(days=800))
    ]
    if earlier.empty or latest.value <= 0:
        return None
    base = earlier.iloc[0]
    if base.value <= 0:
        return None
    years = (latest.period - base.period).days / 365.25
    return clean_number(latest.value * (latest.value / base.value) ** (2 / years))


def build_financial_trends(
    income: pd.DataFrame,
    cashflow: pd.DataFrame,
    *,
    quarterly_income: pd.DataFrame | None = None,
    quarterly_cashflow: pd.DataFrame | None = None,
    revenue_estimate: pd.DataFrame | None = None,
    info: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    info = info or {}
    quarterly_income = quarterly_income if quarterly_income is not None else pd.DataFrame()
    quarterly_cashflow = quarterly_cashflow if quarterly_cashflow is not None else pd.DataFrame()
    statements = {"Revenue": quarterly_income, "Net Income": quarterly_income, "Operating Cash Flow": quarterly_cashflow}
    series = {metric: quarterly_values(statements[metric], names) for metric, names in FINANCIAL_ROWS.items()}
    if not series["Revenue"].empty:
        for metric in ("Net Income", "Operating Cash Flow"):
            aligned = pd.Series(index=series["Revenue"].index, dtype=float)
            for date in aligned.index:
                nearby = series[metric][abs(series[metric].index - date) < pd.Timedelta(days=40)]
                if not nearby.empty:
                    aligned.loc[date] = nearby.iloc[-1]
            series[metric] = aligned
    history = pd.DataFrame(series).sort_index()
    if history.empty:
        return history, history.copy(), pd.DataFrame(columns=history.columns)
    history = history.loc[history.index > history.index[-1] - pd.DateOffset(years=3)]
    forward = pd.DataFrame(index=history.index, columns=history.columns, dtype=float)
    sources = pd.DataFrame("Unavailable", index=history.index, columns=history.columns)
    for frame in (history, forward):
        frame.attrs["financial_currency"] = str(info.get("financialCurrency") or income.attrs.get("financial_currency") or "")
        frame.attrs["basis"] = "Quarterly"
    for period in history.index:
        as_of = _publication_date(quarterly_income, period)
        revenue = _known_quarters(quarterly_income, series["Revenue"], as_of).loc[:period].dropna()
        if revenue.empty or revenue.index[-1] != period or revenue.iloc[-1] <= 0:
            continue
        prior = revenue[
            (revenue.index <= period - pd.Timedelta(days=330))
            & (revenue.index >= period - pd.Timedelta(days=800))
            & (abs((revenue.index.month - period.month + 6) % 12 - 6) <= 1)
        ]
        if not prior.empty and prior.iloc[0] > 0:
            years = (period - prior.index[0]).days / 365.25
            forward.loc[period, "Revenue"] = clean_number(revenue.iloc[-1] * (revenue.iloc[-1] / prior.iloc[0]) ** (2 / years))
            sources.loc[period, "Revenue"] = "Model: same-quarter historical revenue growth, using published data"
        for metric in ("Net Income", "Operating Cash Flow"):
            known = _known_quarters(statements[metric], series[metric], as_of).loc[:period].tail(8)
            margins = (known / revenue.reindex(known.index).where(lambda values: values > 0)).dropna()
            if not margins.empty and pd.notna(forward.loc[period, "Revenue"]):
                forward.loc[period, metric] = clean_number(forward.loc[period, "Revenue"] * margins.median())
                sources.loc[period, metric] = "Model: published historical quarterly margins × forecast quarterly revenue"
    return history, forward, sources


def build_ps_trends(
    income: pd.DataFrame,
    cashflow: pd.DataFrame,
    balance: pd.DataFrame,
    valuation_history: pd.DataFrame,
    *,
    quarterly_income: pd.DataFrame,
    quarterly_balance: pd.DataFrame,
    quarterly_cashflow: pd.DataFrame,
    revenue_estimate: pd.DataFrame | None,
    info: dict[str, Any],
    as_of: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    as_of = pd.Timestamp(as_of if as_of is not None else pd.Timestamp.now(tz="UTC")).tz_localize(None).normalize()
    context = build_historical_ratio_context(
        valuation_history, income, balance, cashflow, years=3,
        quarterly_income=quarterly_income, quarterly_balance=quarterly_balance, quarterly_cashflow=quarterly_cashflow,
    )
    prices = pd.Series(dtype=float)
    if not valuation_history.empty and "Close" in valuation_history:
        prices = pd.to_numeric(valuation_history["Close"], errors="coerce").dropna()
        prices.index = pd.DatetimeIndex(pd.to_datetime(prices.index)).tz_localize(None)
        prices = prices.sort_index().loc[:as_of]
    dates = pd.DatetimeIndex([])
    if not prices.empty:
        dates = pd.date_range(max(prices.index[0], as_of - pd.DateOffset(years=3)), as_of, freq="QE")
    dates = dates.union(pd.DatetimeIndex([as_of]))
    ratios = pd.DataFrame(index=dates, columns=["P/S TTM", "P/S +2Y"], dtype=float)
    sources = pd.DataFrame("Unavailable", index=dates, columns=ratios.columns)
    annual_history, annual_future, annual_sources = _annual_financial_trends(
        income, cashflow, revenue_estimate=revenue_estimate, info=info,
    )
    for date in dates:
        revenue = value_on_or_before(context.revenue, date)
        cap = None
        if date == as_of:
            cap = reporting_market_cap(info)
            revenue = context.current_denominator("ps")[0]
        else:
            available_prices = prices.loc[:date]
            shares = value_on_or_before(context.shares, date)
            if not available_prices.empty and date - available_prices.index[-1] <= pd.Timedelta(days=7) and shares is not None and shares > 0:
                cap = clean_number(available_prices.iloc[-1] * shares)
        if cap is None or cap <= 0 or revenue is None or revenue <= 0:
            continue
        ratios.loc[date, "P/S TTM"] = clean_number(cap / revenue)
        sources.loc[date, "P/S TTM"] = "Market capitalization / published TTM revenue (annual fallback where TTM unavailable)"
        observations = context.periods["revenue"].observations.copy()
        if date == as_of and not observations.empty:
            # A returned statement is already known today, even when the
            # conservative historical 90-day default has not elapsed yet.
            observations["available"] = observations["available"].clip(upper=as_of)
        future_revenue = projected_revenue(observations, date)
        source = "Model: published annual/TTM revenue CAGR projected two years ahead"
        # Only today's point can use today's consensus, and only for a matching
        # two-year fiscal target. Never apply it to historical observation dates.
        if date == as_of and not annual_history.empty:
            target = date + pd.DateOffset(years=2)
            matching = annual_future.index[abs(annual_future.index - target) < pd.Timedelta(days=45)]
            if len(matching) and "Analyst" in annual_sources.loc[matching[0], "Revenue"]:
                future_revenue = clean_number(annual_future.loc[matching[0], "Revenue"])
                source = "Current analyst revenue consensus (Yahoo Finance); constant current market capitalization"
        if future_revenue is not None and future_revenue > 0:
            ratios.loc[date, "P/S +2Y"] = clean_number(cap / future_revenue)
            sources.loc[date, "P/S +2Y"] = source
    return ratios, sources
