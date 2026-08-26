from __future__ import annotations

from typing import Any

from ticker_analyzer.lazy_imports import resolve_export

_EXPORTS = {
    "CompositeProvider": "ticker_analyzer.providers.merge",
    "merge_market_data": "ticker_analyzer.providers.merge",
    "merge_observations": "ticker_analyzer.providers.merge",
    "JsonApiClient": "ticker_analyzer.providers.http",
    "SecClient": "ticker_analyzer.providers.sec",
    "SecCompanyFactsProvider": "ticker_analyzer.providers.sec",
    "empty_market_data": "ticker_analyzer.providers.sec",
    "sec_statement": "ticker_analyzer.providers.sec",
    "latest_sec_filing": "ticker_analyzer.providers.sec",
    "discrete_quarter_records": "ticker_analyzer.providers.sec",
    "total_debt_component_records": "ticker_analyzer.providers.sec",
    "is_discrete_quarter_or_instant": "ticker_analyzer.providers.sec",
    "NbpClient": "ticker_analyzer.providers.clients",
    "FdicClient": "ticker_analyzer.providers.clients",
    "GleifClient": "ticker_analyzer.providers.clients",
    "FinraClient": "ticker_analyzer.providers.clients",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    return resolve_export(name, _EXPORTS, globals(), __name__)
