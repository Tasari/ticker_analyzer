from __future__ import annotations

from typing import Any

from ticker_analyzer.lazy_imports import resolve_export

_EXPORTS = {
    "refresh_large_cap_ranking": "ticker_analyzer.ui.ranking_actions",
    "acquire_refresh_lock": "ticker_analyzer.ui.ranking_actions",
    "refresh_lock_is_stale": "ticker_analyzer.ui.ranking_actions",
    "read_log_tail": "ticker_analyzer.ui.ranking_actions",
    "ranking_refresh_is_complete": "ticker_analyzer.ui.ranking_actions",
    "analyze_selected_tickers": "ticker_analyzer.ui.analysis_actions",
    "analysis_worker_count": "ticker_analyzer.ui.analysis_actions",
    "analyze_tickers_sequentially": "ticker_analyzer.ui.analysis_actions",
    "analyze_one_ticker": "ticker_analyzer.ui.analysis_actions",
    "cached_ticker_analysis": "ticker_analyzer.ui.analysis_actions",
    "ordered_analysis_results": "ticker_analyzer.ui.analysis_actions",
    "search_tickers": "ticker_analyzer.ui.analysis_actions",
    "cached_ticker_search": "ticker_analyzer.ui.analysis_actions",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return resolve_export(name, _EXPORTS, globals(), __name__)
