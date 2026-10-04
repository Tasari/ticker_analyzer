"""Price, dividend and FX histories used by portfolio simulations."""

from __future__ import annotations

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from ticker_analyzer.markets import currency_convention
from ticker_analyzer.portfolio.returns import (
    ACCOUNT_STATEMENT_TICKER,
    ReturnsTable,
    ReturnsTableError,
    analyze_returns_range,
)
from ticker_analyzer.portfolio.simulation import TRAILING_RETURN_PERIODS
from ticker_analyzer.providers.market_data import retry_transient

MAX_SIMULATION_WORKERS = 5


def _fetch_simulation_market_data(
    results: dict[str, dict],
    start_date: date,
    end_date: date,
    base_currency: str,
    *,
    account_returns: ReturnsTable | None = None,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], list[str]]:
    return _fetch_simulation_data(
        results, start_date, end_date, base_currency, account_returns=account_returns, adjusted=False
    )


def _fetch_simulation_histories(
    results: dict[str, dict],
    start_date: date,
    end_date: date,
    base_currency: str,
    *,
    account_returns: ReturnsTable | None = None,
) -> tuple[dict[str, pd.Series], list[str]]:
    prices, _, warnings = _fetch_simulation_data(
        results,
        start_date,
        end_date,
        base_currency,
        account_returns=account_returns,
        adjusted=True,
    )
    return prices, warnings


def _fetch_simulation_data(
    results: dict[str, dict],
    start_date: date,
    end_date: date,
    base_currency: str,
    *,
    account_returns: ReturnsTable | None,
    adjusted: bool,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], list[str]]:
    tickers = list(results)
    warnings = []
    if account_returns is not None:
        tickers.append(ACCOUNT_STATEMENT_TICKER)
        if not adjusted:
            warnings.append(
                f"{ACCOUNT_STATEMENT_TICKER} is already a total-return series, so its dividends cannot be separated "
                "into reinvested and cash components."
            )
    if not tickers:
        return {}, {}, warnings
    history_start = _simulation_history_start(start_date, end_date)

    def fetch_one(ticker: str) -> tuple[str, pd.Series, pd.Series, str | None]:
        try:
            if ticker == ACCOUNT_STATEMENT_TICKER:
                if account_returns is None:
                    raise ReturnsTableError("the imported returns table is no longer available.")
                return (
                    ticker,
                    _account_statement_prices(account_returns, history_start, end_date),
                    pd.Series(dtype=float),
                    None,
                )
            if adjusted:
                prices, dividends = _cached_adjusted_prices(ticker, history_start, end_date), pd.Series(dtype=float)
            else:
                prices, dividends = _cached_market_history(ticker, history_start, end_date)
            currency = str(results[ticker].get("quote_currency") or results[ticker].get("currency") or base_currency)
            prices = _convert_to_base_currency(prices, currency, base_currency, history_start, end_date)
            if not adjusted:
                dividends = _convert_to_base_currency(dividends, currency, base_currency, history_start, end_date)
            return ticker, prices, dividends, None
        except (RuntimeError, ValueError) as exc:
            return ticker, pd.Series(dtype=float), pd.Series(dtype=float), str(exc)

    completed = {}
    with ThreadPoolExecutor(max_workers=min(MAX_SIMULATION_WORKERS, len(tickers))) as executor:
        futures = [executor.submit(fetch_one, ticker) for ticker in tickers]
        for future in as_completed(futures):
            ticker, prices, dividends, error = future.result()
            completed[ticker] = prices, dividends
            if error:
                warnings.append(f"{ticker}: {error} Its allocation remains cash.")
    return (
        {ticker: completed[ticker][0] for ticker in tickers},
        {ticker: completed[ticker][1] for ticker in tickers},
        warnings,
    )


def _simulation_history_start(start_date: date, end_date: date) -> date:
    longest_period_months = max(months for _, months in TRAILING_RETURN_PERIODS)
    boundary = pd.Timestamp(end_date) - pd.DateOffset(months=longest_period_months)
    return min(start_date, boundary.date() - timedelta(days=7))


def _account_statement_prices(
    returns_table: ReturnsTable,
    start_date: date,
    end_date: date,
) -> pd.Series:
    available_start = max(start_date, returns_table.first_month)
    last_month = returns_table.last_month
    available_end = min(
        end_date,
        date(last_month.year, last_month.month, monthrange(last_month.year, last_month.month)[1]),
    )
    if available_end < available_start:
        raise ReturnsTableError("the imported returns table does not overlap the requested history.")
    analysis = analyze_returns_range(
        returns_table,
        available_start,
        available_end,
        initial_capital=100.0,
    )
    return pd.Series(
        [point.value for point in analysis.growth],
        index=pd.to_datetime([point.day for point in analysis.growth]),
        dtype=float,
    )


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def _cached_adjusted_prices(ticker: str, start_date: date, end_date: date) -> pd.Series:
    import yfinance as yf

    try:
        history = retry_transient(
            lambda: yf.Ticker(ticker).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                actions=False,
            )
        )
    except Exception as exc:
        raise RuntimeError(f"price data could not be downloaded: {exc}") from exc
    if history.empty or "Close" not in history:
        raise RuntimeError("no adjusted prices are available for this range.")
    return pd.to_numeric(history["Close"], errors="coerce").dropna()


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def _cached_market_history(
    ticker: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.Series, pd.Series]:
    import yfinance as yf

    try:
        history = retry_transient(
            lambda: yf.Ticker(ticker).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
            )
        )
    except Exception as exc:
        raise RuntimeError(f"market data could not be downloaded: {exc}") from exc
    if history.empty or "Close" not in history:
        raise RuntimeError("no prices are available for this range.")
    prices = pd.to_numeric(history["Close"], errors="coerce").dropna()
    dividends = (
        pd.to_numeric(history["Dividends"], errors="coerce").fillna(0)
        if "Dividends" in history
        else pd.Series(0.0, index=history.index)
    )
    return prices, dividends[dividends > 0]


def _convert_to_base_currency(
    prices: pd.Series,
    source_currency: str,
    base_currency: str,
    start_date: date,
    end_date: date,
) -> pd.Series:
    source, unit_scale = currency_convention(source_currency)
    converted = _daily_series(prices)
    if unit_scale != 1:
        converted = converted * unit_scale
    if not source or source == base_currency:
        return converted
    factor = _daily_series(_cached_fx_factor(source, base_currency, start_date, end_date))
    aligned_factor = factor.reindex(converted.index, method="ffill").bfill()
    if aligned_factor.isna().any():
        raise RuntimeError(f"{source}/{base_currency} exchange-rate history is incomplete.")
    return converted * aligned_factor


def _daily_series(values: pd.Series) -> pd.Series:
    normalized = pd.to_numeric(values, errors="coerce").dropna()
    normalized.index = pd.to_datetime(normalized.index, errors="coerce", utc=True).tz_convert(None).normalize()
    return normalized[~normalized.index.duplicated(keep="last")].sort_index()


@st.cache_data(ttl=3600, max_entries=24, show_spinner=False)
def _cached_fx_factor(source: str, target: str, start_date: date, end_date: date) -> pd.Series:
    direct = _try_fx_history(f"{source}{target}=X", start_date, end_date)
    if not direct.empty:
        return direct
    inverse = _try_fx_history(f"{target}{source}=X", start_date, end_date)
    if inverse.empty or (inverse <= 0).any():
        raise RuntimeError(f"no {source}/{target} exchange-rate history is available.")
    return 1 / inverse


def _try_fx_history(symbol: str, start_date: date, end_date: date) -> pd.Series:
    try:
        return _cached_adjusted_prices(symbol, start_date - timedelta(days=7), end_date)
    except RuntimeError:
        return pd.Series(dtype=float)
