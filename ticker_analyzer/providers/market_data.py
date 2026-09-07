from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
import yfinance as yf

from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData

logger = logging.getLogger(__name__)

CORE_STATEMENT_ROWS = (
    frozenset({"Total Revenue", "Operating Revenue", "Net Income", "Operating Income"}),
    frozenset({"Total Assets", "Stockholders Equity", "Total Debt"}),
    frozenset({"Operating Cash Flow", "Total Cash From Operating Activities", "Free Cash Flow"}),
)
CORE_INFO_FIELDS = frozenset(
    {
        "marketCap",
        "trailingPE",
        "priceToBook",
        "enterpriseToEbitda",
        "totalRevenue",
        "returnOnEquity",
    }
)


def configure_yfinance_cache() -> Path | None:
    """Keep yfinance's SQLite caches in a location writable by hosted runtimes."""
    cache_dir = Path(tempfile.gettempdir()) / "ticker-analyzer" / "yfinance"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_module = getattr(yf, "cache", None)
        setter = getattr(cache_module, "set_cache_location", None)
        if setter is None:
            setter = getattr(yf, "set_tz_cache_location", None)
        if setter is not None:
            setter(str(cache_dir))
        return cache_dir
    except (OSError, RuntimeError):
        logger.warning("Could not configure the yfinance cache directory", exc_info=True)
        return None


YFINANCE_CACHE_DIR = configure_yfinance_cache()


class MarketDataProvider(Protocol):
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ...


class YFinanceProvider:
    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        ticker = yf.Ticker(ticker_symbol)
        growth_start = history_start_date(ranges.growth)
        value_start = history_start_date(ranges.value)
        history_start = min(growth_start, value_start)
        diagnostics: list[dict[str, str]] = []
        fetched_at = datetime.now(UTC)
        value_history = safe_frame(
            lambda: ticker.history(start=history_start, auto_adjust=False, actions=True),
            label="price history",
            diagnostics=diagnostics,
        )
        growth_history = adjusted_price_history(value_history)
        if not value_history.empty and "Adj Close" not in value_history:
            growth_history = safe_frame(
                lambda: ticker.history(start=growth_start, auto_adjust=True),
                label="adjusted price history",
                diagnostics=diagnostics,
            )
        result = MarketData(
            ticker=ticker_symbol,
            info=safe_dict(lambda: ticker.info, label="company info", diagnostics=diagnostics),
            annual_income=safe_statement(
                lambda: ticker.financials,
                label="annual income statement",
                diagnostics=diagnostics,
            ),
            annual_balance=safe_statement(
                lambda: ticker.balance_sheet,
                label="annual balance sheet",
                diagnostics=diagnostics,
            ),
            annual_cashflow=safe_statement(
                lambda: ticker.cashflow,
                label="annual cash flow",
                diagnostics=diagnostics,
            ),
            quarterly_income=safe_statement(
                lambda: ticker.quarterly_financials,
                label="quarterly income statement",
                diagnostics=diagnostics,
            ),
            quarterly_balance=safe_statement(
                lambda: ticker.quarterly_balance_sheet,
                label="quarterly balance sheet",
                diagnostics=diagnostics,
            ),
            quarterly_cashflow=safe_statement(
                lambda: ticker.quarterly_cashflow,
                label="quarterly cash flow",
                diagnostics=diagnostics,
            ),
            growth_history=growth_history,
            # Valuation multiples must use the price shareholders actually paid.
            # Adjusted prices are useful for total-return/growth charts, but applying
            # them to today's share count makes historical P/E and P/S split-sensitive.
            value_history=value_history,
            analyst_targets=safe_dict(lambda: ticker.analyst_price_targets, label="analyst price targets", diagnostics=diagnostics),
            revenue_estimate=safe_frame(lambda: ticker.revenue_estimate, label="revenue estimates", diagnostics=diagnostics),
            earnings_estimate=safe_frame(lambda: ticker.earnings_estimate, label="earnings estimates", diagnostics=diagnostics),
            eps_trend=safe_frame(lambda: ticker.eps_trend, label="EPS trend", diagnostics=diagnostics),
            growth_estimates=safe_frame(lambda: ticker.growth_estimates, label="growth estimates", diagnostics=diagnostics),
            diagnostics=diagnostics,
        )
        result.value_history = valuation_price_history(result.value_history, result.info.get("currency"))
        result.provenance = build_yfinance_provenance(result, fetched_at)
        fill_missing_core_data(result, ranges)
        return result


def fill_missing_core_data(data: MarketData, ranges: AnalysisRanges) -> None:
    """Recover core prices/statements when yfinance returns a partial response."""
    annual_statements = (data.annual_income, data.annual_balance, data.annual_cashflow)
    missing_prices = data.value_history.empty
    meaningful_statements = sum(
        statement_has_any_row(frame, expected_rows)
        for frame, expected_rows in zip(annual_statements, CORE_STATEMENT_ROWS, strict=True)
    )
    missing_financials = meaningful_statements < 2
    has_price = clean_info_price(data.info) is not None
    missing_profile = not (data.info.get("industry") or data.info.get("industryDisp") or data.info.get("sector"))
    sparse_info = sum(data.info.get(field) is not None for field in CORE_INFO_FIELDS) < 2
    needs_fallback = missing_prices or not has_price or missing_financials or missing_profile or sparse_info
    if not needs_fallback:
        return

    try:
        # The public chart and fundamentals-timeseries endpoints do not depend on
        # yfinance's crumb/cookie state and are therefore a useful narrow fallback.
        from ticker_analyzer.providers.merge import merge_market_data
        from ticker_analyzer.ranking.provider import PublicYahooRankingProvider

        fallback = PublicYahooRankingProvider(
            {
                data.ticker: {
                    "company_name": data.info.get("longName") or data.info.get("shortName"),
                    "market_cap": data.info.get("marketCap"),
                    "industry": data.info.get("industry"),
                    "sector": data.info.get("sector"),
                }
            },
            enrich_profile=True,
        ).fetch(data.ticker, ranges)
        merge_market_data(data, fallback)
        if missing_prices and "prices" in fallback.provenance:
            data.provenance["prices"] = fallback.provenance["prices"]
        if missing_financials and "financials" in fallback.provenance:
            data.provenance["financials"] = fallback.provenance["financials"]
        data.diagnostics.extend(fallback.diagnostics)
    except Exception as exc:
        record_fetch_failure("public Yahoo core-data fallback", exc, data.diagnostics)


def statement_has_any_row(frame: pd.DataFrame, expected_rows: frozenset[str]) -> bool:
    if frame.empty:
        return False
    return any(str(row) in expected_rows for row in frame.index)


def valuation_price_history(history: pd.DataFrame, currency: Any) -> pd.DataFrame:
    """Convert London pence quotes to the pounds used by financial statements."""
    if history.empty or "Close" not in history or not is_minor_gbp_currency(currency):
        return history
    normalized = history.copy()
    normalized["Close"] = pd.to_numeric(normalized["Close"], errors="coerce") / 100
    if "Adj Close" in normalized:
        normalized["Adj Close"] = pd.to_numeric(normalized["Adj Close"], errors="coerce") / 100
    return normalized


def is_minor_gbp_currency(currency: Any) -> bool:
    label = str(currency or "").strip()
    return label == "GBp" or label.upper() == "GBX"


def clean_info_price(info: dict[str, Any]) -> float | None:
    for key in ("currentPrice", "regularMarketPrice"):
        try:
            value = float(info.get(key))
        except (TypeError, ValueError):
            continue
        if pd.notna(value):
            return value
    return None


def adjusted_price_history(history: pd.DataFrame) -> pd.DataFrame:
    """Build the adjusted-price view from the raw history response when available."""
    adjusted = history.copy()
    if not adjusted.empty and "Adj Close" in adjusted:
        adjusted["Close"] = adjusted["Adj Close"]
    return adjusted


def safe_frame(
    callback: Callable[[], Any],
    *,
    label: str = "data frame",
    diagnostics: list[dict[str, str]] | None = None,
) -> pd.DataFrame:
    try:
        value = retry_transient(callback)
    except Exception as exc:
        record_fetch_failure(label, exc, diagnostics)
        return pd.DataFrame()
    if value is None:
        return pd.DataFrame()
    return value.copy() if isinstance(value, pd.DataFrame) else pd.DataFrame(value)


def safe_statement(
    callback: Callable[[], Any],
    *,
    label: str = "financial statement",
    diagnostics: list[dict[str, str]] | None = None,
) -> pd.DataFrame:
    """Fetch and normalize a statement while taking ownership with one copy."""
    return normalize_statement(
        safe_frame(callback, label=label, diagnostics=diagnostics),
        copy=False,
    )


def safe_dict(
    callback: Callable[[], Any],
    *,
    label: str = "data",
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    try:
        value = retry_transient(callback)
    except Exception as exc:
        record_fetch_failure(label, exc, diagnostics)
        return {}
    return value if isinstance(value, dict) else {}


def retry_transient(
    callback: Callable[[], Any],
    *,
    attempts: int = 2,
    initial_delay: float = 0.25,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return callback()
        except Exception as exc:
            last_error = exc
            if attempt + 1 >= attempts or not is_transient_provider_error(exc):
                raise
            time.sleep(initial_delay * (2**attempt))
    raise last_error or RuntimeError("provider request failed")


def is_transient_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    text = f"{type(exc).__name__} {exc}".casefold()
    return any(
        marker in text
        for marker in (
            "timeout",
            "connection",
            "network",
            "too many requests",
            "rate limit",
            "unauthorized",
            "invalid crumb",
            "status code 401",
            "http error 401",
            "status code 429",
            "status code 500",
            "status code 502",
            "status code 503",
            "status code 504",
        )
    )


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


def normalize_statement(frame: pd.DataFrame, *, copy: bool = True) -> pd.DataFrame:
    if frame.empty:
        return frame
    if copy:
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


def build_yfinance_provenance(data: MarketData, fetched_at: datetime) -> dict[str, DataProvenance]:
    return {
        "financials": DataProvenance(
            provider="yfinance",
            fetched_at=fetched_at,
            period_end=latest_statement_period(data),
            observation_count=max(
                len(data.annual_income.columns),
                len(data.annual_balance.columns),
                len(data.annual_cashflow.columns),
            ),
            fallback_level="secondary_source",
            is_primary_source=False,
        ),
        "prices": DataProvenance(
            provider="yfinance",
            fetched_at=fetched_at,
            period_end=_latest_index(data.value_history),
            observation_count=len(data.value_history),
            fallback_level="secondary_source",
            is_primary_source=False,
        ),
        "estimates": DataProvenance(
            provider="yfinance",
            fetched_at=fetched_at,
            observation_count=max(len(data.revenue_estimate), len(data.earnings_estimate)),
            fallback_level="estimated",
            is_primary_source=False,
        ),
    }


def latest_statement_period(data: MarketData) -> datetime | None:
    dates = []
    for frame in (
        data.quarterly_income,
        data.quarterly_balance,
        data.quarterly_cashflow,
        data.annual_income,
        data.annual_balance,
        data.annual_cashflow,
    ):
        if not frame.empty:
            dates.extend(pd.to_datetime(frame.columns, errors="coerce").dropna().tolist())
    if not dates:
        return None
    return pd.Timestamp(max(dates)).to_pydatetime().replace(tzinfo=UTC)


def _latest_index(frame: pd.DataFrame) -> datetime | None:
    if frame.empty:
        return None
    dates = pd.to_datetime(frame.index, errors="coerce").dropna()
    if dates.empty:
        return None
    return pd.Timestamp(dates.max()).to_pydatetime().replace(tzinfo=UTC)
