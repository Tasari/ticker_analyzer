from __future__ import annotations

import pandas as pd

from ticker_analyzer.providers.etf import normalize_holding_ticker
from ticker_analyzer.ranking.storage import ETF_RANKING_PATH, load_ranking
from ticker_analyzer.ticker_symbols import deduplicate_ticker_options, looks_like_ticker, normalize_ticker
from ticker_analyzer.ui.analysis_actions import cached_ticker_search


def search_etfs(searchterm: str) -> list[str]:
    """Suggest funds by name or symbol, including the local ranking when Yahoo is down."""
    query = searchterm.strip()
    normalized_query = query.casefold()
    try:
        rows = load_ranking(ETF_RANKING_PATH).get("companies", [])
    except (OSError, ValueError):
        rows = []
    suggestions = []
    for row in rows:
        ticker = normalize_ticker(row.get("ticker"))
        name = str(row.get("name") or ticker or "")
        if ticker and (not query or normalized_query in f"{ticker} {name}".casefold()):
            suggestions.append(f"{ticker} | {name} | {row.get('market') or row.get('exchange') or ''}".rstrip(" |"))
    if query:
        suggestions.extend(cached_ticker_search(query, quote_type="ETF"))
    exact = normalize_ticker(query) if looks_like_ticker(query) else None
    if exact and exact != "ACC_STMT":
        suggestions.append(f"{exact} | Add exact ETF ticker")
    return deduplicate_ticker_options(suggestions)[:20]


def selected_holding_tickers(displayed: pd.DataFrame, rows: list[int], source: str) -> list[str]:
    tickers = []
    for row in rows:
        if not isinstance(row, int) or not 0 <= row < len(displayed):
            continue
        ticker = normalize_holding_ticker(displayed.iloc[row]["Ticker"], source)
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers
