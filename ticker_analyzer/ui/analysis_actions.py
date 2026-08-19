from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import streamlit as st

from ticker_analyzer import analyze_ticker

logger = logging.getLogger(__name__)
MAX_ANALYSIS_WORKERS = 5
MAX_ANALYSIS_CACHE_ENTRIES = 32
MAX_SEARCH_CACHE_ENTRIES = 128
AnalysisResult = dict[str, Any]
AnalysisError = str
TickerAnalysisOutcome = tuple[AnalysisResult | None, AnalysisError | None]


def analyze_selected_tickers(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    if len(tickers) <= 1:
        return analyze_tickers_sequentially(tickers, ranges, config)

    completed: dict[str, TickerAnalysisOutcome] = {}
    with ThreadPoolExecutor(max_workers=analysis_worker_count(tickers)) as executor:
        futures = {
            executor.submit(analyze_one_ticker, ticker, ranges, config): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            completed[ticker] = future.result()
    return ordered_analysis_results(tickers, completed)


def analysis_worker_count(tickers: list[str]) -> int:
    return min(MAX_ANALYSIS_WORKERS, max(1, len(tickers)))


def analyze_tickers_sequentially(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    completed = {
        ticker: analyze_one_ticker(ticker, ranges, config)
        for ticker in tickers
    }
    return ordered_analysis_results(tickers, completed)


def analyze_one_ticker(ticker: str, ranges: dict[str, str], config: dict) -> TickerAnalysisOutcome:
    try:
        return cached_ticker_analysis(ticker, ranges, config, id(analyze_ticker)), None
    except ValueError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("Unexpected analysis failure for %s", ticker)
        return None, "Unexpected internal error. Check application logs."


@st.cache_data(ttl=900, max_entries=MAX_ANALYSIS_CACHE_ENTRIES, show_spinner=False)
def cached_ticker_analysis(
    ticker: str,
    ranges: dict[str, str],
    config: dict,
    analyzer_identity: int,
) -> AnalysisResult:
    """Cache successful analyses independently so expanding a comparison is incremental."""
    del analyzer_identity  # Included only to isolate patched/test analyzer implementations.
    return analyze_ticker(ticker, ranges, config)


def ordered_analysis_results(
    tickers: list[str],
    completed: dict[str, TickerAnalysisOutcome],
) -> tuple[dict, dict]:
    results = {}
    errors = {}
    for ticker in tickers:
        result, error = completed.get(ticker, (None, "Analysis did not complete."))
        if error is not None:
            errors[ticker] = error
        elif result is not None:
            results[ticker] = result
    return results, errors


def search_tickers(searchterm: str) -> list[str]:
    return cached_ticker_search(searchterm.strip())


@st.cache_data(ttl=900, max_entries=MAX_SEARCH_CACHE_ENTRIES, show_spinner=False)
def cached_ticker_search(query: str) -> list[str]:
    import yfinance as yf

    if len(query) < 1:
        return []
    try:
        results = yf.Search(
            query,
            max_results=10,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            enable_fuzzy_query=True,
            recommended=0,
        ).quotes
    except Exception:
        logger.warning("Ticker search failed for query %s", query, exc_info=True)
        return []

    suggestions = []
    for result in results:
        if result.get("quoteType") != "EQUITY":
            continue
        symbol = result.get("symbol")
        if not symbol:
            continue
        name = result.get("longname") or result.get("shortname") or symbol
        exchange = result.get("exchDisp") or result.get("exchange") or ""
        suggestions.append(f"{symbol} | {name} | {exchange}".rstrip(" |"))
    return suggestions

