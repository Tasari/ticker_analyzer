from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "render_sidebar": "ticker_analyzer.ui.sidebar",
    "render_ticker_search": "ticker_analyzer.ui.sidebar",
    "render_selected_tickers": "ticker_analyzer.ui.sidebar",
    "remove_selected_tickers": "ticker_analyzer.ui.sidebar",
    "render_analysis_errors": "ticker_analyzer.ui.analysis_views",
    "render_company_analysis": "ticker_analyzer.ui.analysis_views",
    "render_multi_ticker_analysis": "ticker_analyzer.ui.analysis_views",
    "render_comparison_summary": "ticker_analyzer.ui.analysis_views",
    "rank_results": "ticker_analyzer.ui.analysis_views",
    "render_ranking": "ticker_analyzer.ui.analysis_views",
    "render_company_cards": "ticker_analyzer.ui.analysis_views",
    "render_comparison_table": "ticker_analyzer.ui.analysis_views",
    "render_metric_comparison": "ticker_analyzer.ui.analysis_views",
    "format_company_price": "ticker_analyzer.ui.analysis_views",
    "format_score": "ticker_analyzer.ui.analysis_views",
    "format_tab_summary": "ticker_analyzer.ui.analysis_views",
    "format_coverage": "ticker_analyzer.ui.analysis_views",
    "quality_label": "ticker_analyzer.ui.analysis_views",
    "data_quality_value": "ticker_analyzer.ui.analysis_views",
    "format_data_quality": "ticker_analyzer.ui.analysis_views",
    "render_summary": "ticker_analyzer.ui.analysis_views",
    "render_tabs": "ticker_analyzer.ui.analysis_views",
    "render_tab": "ticker_analyzer.ui.analysis_views",
    "render_line_chart": "ticker_analyzer.ui.analysis_views",
    "render_metrics_table": "ticker_analyzer.ui.analysis_views",
    "render_large_cap_ranking": "ticker_analyzer.ui.ranking_view",
    "add_ranking_tickers_to_analyzer": "ticker_analyzer.ui.ranking_view",
    "render_account_statement": "ticker_analyzer.ui.account_statement_view",
    "render_config_editor": "ticker_analyzer.ui.config_view",
    "mutation_allowed": "ticker_analyzer.ui.config_view",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
