from __future__ import annotations

from dataclasses import fields
from typing import Any

import pandas as pd

from ticker_analyzer.domain import MarketData


class CompositeProvider:
    """Merge providers in priority order while retaining source diagnostics.

    Earlier providers win for populated scalar fields. Later providers fill gaps,
    which makes an official-source provider + yfinance fallback deterministic.
    """

    def __init__(self, providers: Iterable[Any]) -> None:
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("CompositeProvider requires at least one provider.")

    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        results: list[MarketData] = []
        failures: list[dict[str, str]] = []
        for provider in self.providers:
            try:
                results.append(provider.fetch(ticker_symbol, ranges))
            except Exception as exc:
                failures.append(
                    {
                        "source": type(provider).__name__,
                        "kind": "provider_error",
                        "message": (str(exc).strip() or type(exc).__name__)[:240],
                    }
                )
        if not results:
            raise RuntimeError(f"All market-data providers failed for {ticker_symbol}.")
        merged = results[0]
        for fallback in results[1:]:
            merge_market_data(merged, fallback)
        merged.diagnostics = failures + [item for result in results for item in result.diagnostics]
        return merged


def merge_market_data(primary: MarketData, fallback: MarketData) -> None:
    for field in fields(MarketData):
        name = field.name
        if name in {"ticker", "diagnostics", "provenance", "official_ids"}:
            continue
        current = getattr(primary, name)
        other = getattr(fallback, name)
        if isinstance(current, pd.DataFrame):
            if isinstance(other, pd.DataFrame) and not other.empty:
                setattr(primary, name, merge_observations(current, other))
        elif isinstance(current, dict) and isinstance(other, dict):
            setattr(primary, name, {**other, **{key: value for key, value in current.items() if value is not None}})
    primary.provenance = {**fallback.provenance, **primary.provenance}
    primary.official_ids = {**fallback.official_ids, **primary.official_ids}


def merge_observations(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """Merge statement observations cell-by-cell, preserving provider priority."""
    if primary.empty:
        return fallback.copy()
    merged = primary.combine_first(fallback).sort_index(axis=1)
    provenance: dict[Any, Any] = {}
    reconciliation = [
        *fallback.attrs.get("reconciliation", []),
        *primary.attrs.get("reconciliation", []),
    ]
    fallback_provenance = fallback.attrs.get("observation_provenance", {})
    primary_provenance = primary.attrs.get("observation_provenance", {})
    for row in merged.index:
        for period in merged.columns:
            key = (row, period)
            if row in primary.index and period in primary.columns and pd.notna(primary.at[row, period]):
                if row in fallback.index and period in fallback.columns and pd.notna(fallback.at[row, period]):
                    left = pd.to_numeric(pd.Series([primary.at[row, period]]), errors="coerce").iloc[0]
                    right = pd.to_numeric(pd.Series([fallback.at[row, period]]), errors="coerce").iloc[0]
                    if pd.notna(left) and pd.notna(right):
                        scale = max(abs(float(left)), abs(float(right)), 1.0)
                        reconciliation.append(
                            {
                                "fact": str(row),
                                "period_end": _period_label(period),
                                "relative_difference": abs(float(left) - float(right)) / scale,
                            }
                        )
                if key in primary_provenance:
                    provenance[key] = primary_provenance[key]
            elif key in fallback_provenance:
                provenance[key] = fallback_provenance[key]
    merged.attrs.update(fallback.attrs)
    merged.attrs.update(primary.attrs)
    merged.attrs["observation_provenance"] = provenance
    merged.attrs["filed_dates"] = {**fallback.attrs.get("filed_dates", {}), **primary.attrs.get("filed_dates", {})}
    merged.attrs["reconciliation"] = reconciliation
    return merged


def _period_label(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return str(value) if pd.isna(parsed) else pd.Timestamp(parsed).date().isoformat()

