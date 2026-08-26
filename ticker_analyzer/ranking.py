from __future__ import annotations

from typing import Any

from ticker_analyzer.lazy_imports import resolve_export

_EXPORTS = {
    "DEFAULT_RANKING_PATH": "ticker_analyzer.ranking_storage",
    "load_ranking": "ticker_analyzer.ranking_storage",
    "save_ranking": "ticker_analyzer.ranking_storage",
    "export_ranking": "ticker_analyzer.ranking_storage",
    "import_ranking": "ticker_analyzer.ranking_storage",
    "validate_ranking_payload": "ticker_analyzer.ranking_storage",
    "RankingSnapshotError": "ticker_analyzer.ranking_storage",
    "UNIVERSE_SCHEMA_VERSION": "ticker_analyzer.ranking_universe",
    "US_EXCHANGES": "ticker_analyzer.ranking_universe",
    "XTB_EXCHANGE_MARKETS": "ticker_analyzer.ranking_universe",
    "TRADINGVIEW_COLUMNS": "ticker_analyzer.ranking_universe",
    "normalize_ticker": "ticker_analyzer.ranking_universe",
    "fetch_large_cap_universe": "ticker_analyzer.ranking_universe",
    "yahoo_ticker_from_tradingview": "ticker_analyzer.ranking_universe",
    "fetch_tradingview_market_universe": "ticker_analyzer.ranking_universe",
    "fetch_large_cap_universe_nasdaq": "ticker_analyzer.ranking_universe",
    "select_exchange_listings": "ticker_analyzer.ranking_universe",
    "combine_exchange_universes": "ticker_analyzer.ranking_universe",
    "merge_large_cap_universes": "ticker_analyzer.ranking_universe",
    "market_counts": "ticker_analyzer.ranking_universe",
    "validate_market_coverage": "ticker_analyzer.ranking_universe",
    "checkpoint_universe_is_current": "ticker_analyzer.ranking_universe",
    "SCORING_VERSION": "ticker_analyzer.ranking_builder",
    "PROVIDER_SCHEMA_VERSION": "ticker_analyzer.ranking_builder",
    "METRIC_SCHEMA_VERSION": "ticker_analyzer.ranking_builder",
    "config_digest": "ticker_analyzer.ranking_builder",
    "analysis_fingerprint": "ticker_analyzer.ranking_builder",
    "ranking_row": "ticker_analyzer.ranking_builder",
    "sort_ranking": "ticker_analyzer.ranking_builder",
    "build_large_cap_ranking": "ticker_analyzer.ranking_builder",
    "ranking_payload": "ticker_analyzer.ranking_builder",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return resolve_export(name, _EXPORTS, globals(), __name__)
