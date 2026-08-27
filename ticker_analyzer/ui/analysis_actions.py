from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import streamlit as st

from ticker_analyzer import analyze_ticker
from ticker_analyzer.ticker_symbols import looks_like_ticker, normalize_ticker

logger = logging.getLogger(__name__)
MAX_ANALYSIS_WORKERS = 5
MAX_ANALYSIS_CACHE_ENTRIES = 32
MAX_SEARCH_CACHE_ENTRIES = 128
AnalysisResult = dict[str, Any]
AnalysisError = str
TickerAnalysisOutcome = tuple[AnalysisResult | None, AnalysisError | None]


def analyze_selected_tickers(
    tickers: list[str],
    ranges: dict[str, str],
    config: dict,
    *,
    cache_token: int = 0,
) -> tuple[dict, dict]:
    if len(tickers) <= 1:
        return analyze_tickers_sequentially(tickers, ranges, config, cache_token=cache_token)

    completed: dict[str, TickerAnalysisOutcome] = {}
    with ThreadPoolExecutor(max_workers=analysis_worker_count(tickers)) as executor:
        futures = {
            executor.submit(analyze_one_ticker, ticker, ranges, config, cache_token): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            completed[ticker] = future.result()
    return ordered_analysis_results(tickers, completed)


def analysis_worker_count(tickers: list[str]) -> int:
    return min(MAX_ANALYSIS_WORKERS, max(1, len(tickers)))


def analyze_tickers_sequentially(
    tickers: list[str],
    ranges: dict[str, str],
    config: dict,
    *,
    cache_token: int = 0,
) -> tuple[dict, dict]:
    completed = {
        ticker: analyze_one_ticker(ticker, ranges, config, cache_token)
        for ticker in tickers
    }
    return ordered_analysis_results(tickers, completed)


def analyze_one_ticker(
    ticker: str,
    ranges: dict[str, str],
    config: dict,
    cache_token: int = 0,
) -> TickerAnalysisOutcome:
    try:
        return cached_ticker_analysis(ticker, ranges, config, id(analyze_ticker), cache_token), None
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
    cache_token: int = 0,
) -> AnalysisResult:
    """Cache successful analyses independently so expanding a comparison is incremental."""
    del analyzer_identity, cache_token  # Cache isolation keys; analysis itself does not use them.
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
    query = searchterm.strip()
    results = cached_ticker_search(query)
    exact = normalize_ticker(query) if looks_like_ticker(query) else None
    if exact:
        exact_option = f"{exact} | Add exact Yahoo ticker"
        results = [exact_option, *results]
    deduplicated = []
    seen: set[str] = set()
    for result in results:
        ticker = result.split(" | ", maxsplit=1)[0]
        if ticker in seen:
            continue
        seen.add(ticker)
        deduplicated.append(result)
    return deduplicated


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
        logger.warning("Ticker search failed for query %s", query)
        return []

    suggestions = []
    for result in results:
        if result.get("quoteType") not in {"EQUITY", "ETF"}:
            continue
        symbol = normalize_ticker(result.get("symbol"))
        if not symbol:
            continue
        name = result.get("longname") or result.get("shortname") or symbol
        exchange = result.get("exchDisp") or result.get("exchange") or ""
        suggestions.append(f"{symbol} | {name} | {exchange}".rstrip(" |"))
    return suggestions
