from __future__ import annotations

from typing import Any, Protocol

import pandas as pd
import yfinance as yf

from ticker_analyzer.domain import AnalysisRanges, MarketData


class MarketDataProvider(Protocol):
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ...


class YFinanceProvider:
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ticker = yf.Ticker(ticker_symbol)
        growth_start = history_start_date(ranges.growth)
        value_start = history_start_date(ranges.value)
        return MarketData(
            ticker=ticker_symbol,
            info=safe_dict(lambda: ticker.info),
            annual_income=normalize_statement(safe_frame(lambda: ticker.financials)),
            annual_balance=normalize_statement(safe_frame(lambda: ticker.balance_sheet)),
            annual_cashflow=normalize_statement(safe_frame(lambda: ticker.cashflow)),
            quarterly_income=normalize_statement(safe_frame(lambda: ticker.quarterly_financials)),
            quarterly_balance=normalize_statement(safe_frame(lambda: ticker.quarterly_balance_sheet)),
            growth_history=safe_frame(lambda: ticker.history(start=growth_start, auto_adjust=True)),
            value_history=safe_frame(lambda: ticker.history(start=value_start, auto_adjust=True)),
            earnings_dates=safe_frame(lambda: ticker.get_earnings_dates(limit=16)),
            analyst_targets=safe_dict(lambda: ticker.analyst_price_targets),
            revenue_estimate=safe_frame(lambda: ticker.revenue_estimate),
            earnings_estimate=safe_frame(lambda: ticker.earnings_estimate),
            eps_trend=safe_frame(lambda: ticker.eps_trend),
            growth_estimates=safe_frame(lambda: ticker.growth_estimates),
        )


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


def history_start_date(range_label: str) -> str:
    years = range_years(range_label)
    start = pd.Timestamp.today(tz="UTC").tz_localize(None) - pd.DateOffset(years=years)
    return start.date().isoformat()


def range_years(range_label: str) -> int:
    normalized = str(range_label or "3Y").strip().lower()
    if normalized.endswith("y"):
        try:
            return max(1, int(normalized[:-1]))
        except ValueError:
            return 3
    return 3
