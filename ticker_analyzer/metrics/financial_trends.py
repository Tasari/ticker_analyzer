"""Annual chart history and explicitly sourced, chart-only projections."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.metrics.estimates import estimate_row
from ticker_analyzer.metrics.utils import clean_number, row_values


def build_financial_trends(
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
