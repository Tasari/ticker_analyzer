"""Ranking monetary amounts are explicitly USD; quote prices retain their currency."""
from __future__ import annotations

from typing import Any

from ticker_analyzer.markets import currency_convention
from ticker_analyzer.numbers import clean_number
from ticker_analyzer.providers.fx import exchange_rate


def usd_amount_fields(field: str, value: Any, currency: str) -> dict[str, Any]:
    amount = clean_number(value)
    currency = currency_convention(currency)[0]
    factor = exchange_rate(currency, "USD") if currency and amount is not None else None
    return {
        field: amount * factor if amount is not None and factor is not None else None,
        f"{field}_currency": "USD",
        f"{field}_original": amount,
        f"{field}_original_currency": currency,
        f"{field}_fx_rate": factor,
    }


def usd_market_cap(row: dict[str, Any]) -> float | None:
    # Legacy imported snapshots have no currency contract. Keep them readable,
    # but never use an unlabelled amount in a USD filter or cross-market sort.
    if row.get("market_cap_currency") != "USD":
        return None
    return clean_number(row.get("market_cap"))
