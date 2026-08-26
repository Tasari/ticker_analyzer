from __future__ import annotations

from typing import Any

from ticker_analyzer.lazy_imports import resolve_export

_EXPORTS = {
    "CompositeProvider": "ticker_analyzer.provider_merge",
    "merge_market_data": "ticker_analyzer.provider_merge",
    "merge_observations": "ticker_analyzer.provider_merge",
    "JsonApiClient": "ticker_analyzer.provider_http",
    "SecClient": "ticker_analyzer.provider_sec",
    "SecCompanyFactsProvider": "ticker_analyzer.provider_sec",
    "empty_market_data": "ticker_analyzer.provider_sec",
    "sec_statement": "ticker_analyzer.provider_sec",
    "latest_sec_filing": "ticker_analyzer.provider_sec",
    "discrete_quarter_records": "ticker_analyzer.provider_sec",
    "total_debt_component_records": "ticker_analyzer.provider_sec",
    "is_discrete_quarter_or_instant": "ticker_analyzer.provider_sec",
    "NbpClient": "ticker_analyzer.provider_clients",
    "FdicClient": "ticker_analyzer.provider_clients",
    "GleifClient": "ticker_analyzer.provider_clients",
    "FinraClient": "ticker_analyzer.provider_clients",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return resolve_export(name, _EXPORTS, globals(), __name__)
