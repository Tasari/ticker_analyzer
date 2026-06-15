from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

import pandas as pd
import yfinance as yf

from ticker_analyzer.domain import AnalysisRanges, MarketData

logger = logging.getLogger(__name__)


class MarketDataProvider(Protocol):
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ...


class YFinanceProvider:
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ticker = yf.Ticker(ticker_symbol)
        growth_start = history_start_date(ranges.growth)
        value_start = history_start_date(ranges.value)
        diagnostics: list[dict[str, str]] = []
        return MarketData(
            ticker=ticker_symbol,
            info=safe_dict(lambda: ticker.info, label="company info", diagnostics=diagnostics),
            annual_income=normalize_statement(safe_frame(lambda: ticker.financials, label="annual income statement", diagnostics=diagnostics)),
            annual_balance=normalize_statement(safe_frame(lambda: ticker.balance_sheet, label="annual balance sheet", diagnostics=diagnostics)),
            annual_cashflow=normalize_statement(safe_frame(lambda: ticker.cashflow, label="annual cash flow", diagnostics=diagnostics)),
            quarterly_income=normalize_statement(safe_frame(lambda: ticker.quarterly_financials, label="quarterly income statement", diagnostics=diagnostics)),
            quarterly_balance=normalize_statement(safe_frame(lambda: ticker.quarterly_balance_sheet, label="quarterly balance sheet", diagnostics=diagnostics)),
            quarterly_cashflow=normalize_statement(safe_frame(lambda: ticker.quarterly_cashflow, label="quarterly cash flow", diagnostics=diagnostics)),
            growth_history=safe_frame(lambda: ticker.history(start=growth_start, auto_adjust=True), label="growth price history", diagnostics=diagnostics),
            value_history=safe_frame(lambda: ticker.history(start=value_start, auto_adjust=True), label="value price history", diagnostics=diagnostics),
            analyst_targets=safe_dict(lambda: ticker.analyst_price_targets, label="analyst price targets", diagnostics=diagnostics),
            revenue_estimate=safe_frame(lambda: ticker.revenue_estimate, label="revenue estimates", diagnostics=diagnostics),
            earnings_estimate=safe_frame(lambda: ticker.earnings_estimate, label="earnings estimates", diagnostics=diagnostics),
            eps_trend=safe_frame(lambda: ticker.eps_trend, label="EPS trend", diagnostics=diagnostics),
            growth_estimates=safe_frame(lambda: ticker.growth_estimates, label="growth estimates", diagnostics=diagnostics),
            diagnostics=diagnostics,
        )


def safe_frame(
    callback: Callable[[], Any],
    *,
    label: str = "data frame",
    diagnostics: list[dict[str, str]] | None = None,
) -> pd.DataFrame:
    try:
        value = callback()
    except Exception as exc:
        record_fetch_failure(label, exc, diagnostics)
        return pd.DataFrame()
    if value is None:
        return pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def safe_dict(
    callback: Callable[[], Any],
    *,
    label: str = "data",
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        value = callback()
    except Exception as exc:
        record_fetch_failure(label, exc, diagnostics)
        return {}
    return value if isinstance(value, dict) else {}


def record_fetch_failure(
    label: str,
    exc: Exception,
    diagnostics: list[dict[str, str]] | None,
) -> None:
    exception_name = type(exc).__name__.lower()
    is_network_error = isinstance(exc, (ConnectionError, TimeoutError)) or any(
        marker in exception_name for marker in ("timeout", "connection", "network")
    )
    kind = "network_error" if is_network_error else "provider_error"
    message = str(exc).strip() or type(exc).__name__
    logger.warning("yfinance fetch failed for %s: %s", label, message)
    logger.debug("Detailed yfinance failure for %s", label, exc_info=True)
    if diagnostics is not None:
        diagnostics.append({"source": label, "kind": kind, "message": message[:240]})


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
    normalized = str(range_label or "2Y").strip().lower()
    if normalized.endswith("y"):
        try:
            return max(1, int(normalized[:-1]))
        except ValueError:
            return 2
    return 2
