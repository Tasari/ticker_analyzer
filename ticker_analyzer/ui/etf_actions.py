from __future__ import annotations

from ticker_analyzer.ranking.storage import ETF_RANKING_PATH, load_ranking
from ticker_analyzer.ticker_symbols import looks_like_ticker, normalize_ticker
from ticker_analyzer.ui.analysis_actions import cached_ticker_search


def search_etfs(searchterm: str) -> list[str]:
    """Suggest funds by name or symbol, including the local ranking when Yahoo is down."""
    query = searchterm.strip()
    try:
        rows = load_ranking(ETF_RANKING_PATH).get("companies", [])
    except (OSError, ValueError):
        rows = []
    suggestions = []
    for row in rows:
        ticker = normalize_ticker(row.get("ticker"))
        name = str(row.get("name") or ticker or "")
        if ticker and (not query or query.casefold() in f"{ticker} {name}".casefold()):
            suggestions.append(f"{ticker} | {name} | {row.get('market') or row.get('exchange') or ''}".rstrip(" |"))
    if query:
        suggestions.extend(cached_ticker_search(query, quote_type="ETF"))
    exact = normalize_ticker(query) if looks_like_ticker(query) else None
    if exact and exact != "ACC_STMT":
        suggestions.append(f"{exact} | Add exact ETF ticker")
    result = []
    seen = set()
    for suggestion in suggestions:
        ticker = suggestion.split(" | ", maxsplit=1)[0]
        if ticker not in seen:
            seen.add(ticker)
            result.append(suggestion)
    return result[:20]
