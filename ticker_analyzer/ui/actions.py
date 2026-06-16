from __future__ import annotations

import logging

import streamlit as st
import yfinance as yf

from ticker_analyzer import analyze_ticker

logger = logging.getLogger(__name__)


def analyze_selected_tickers(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    results = {}
    errors = {}
    for ticker in tickers:
        try:
            results[ticker] = analyze_ticker(ticker, ranges, config)
        except ValueError as exc:
            errors[ticker] = str(exc)
        except Exception:
            logger.exception("Unexpected analysis failure for %s", ticker)
            errors[ticker] = "Unexpected internal error. Check application logs."
    return results, errors


@st.cache_data(ttl=900, show_spinner=False)
def search_tickers(searchterm: str) -> list[str]:
    query = searchterm.strip()
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
