from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ticker_analyzer.markets import MARKET_SUFFIXES
from ticker_analyzer.portfolio.returns import ACCOUNT_STATEMENT_TICKER

TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,32}$")


def normalize_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper().replace("/", "-")
    if ticker == ACCOUNT_STATEMENT_TICKER:
        return ticker
    return ticker if TICKER_PATTERN.fullmatch(ticker) else None


def ticker_for_market(value: Any, market: str) -> str | None:
    ticker = normalize_ticker(value)
    if not ticker:
        return None
    suffix = MARKET_SUFFIXES.get(market)
    if suffix is None or not suffix or ticker.endswith(suffix):
        return ticker
    return normalize_ticker(f"{ticker}{suffix}")


def looks_like_ticker(value: Any) -> bool:
    raw = str(value or "").strip()
    normalized = normalize_ticker(raw)
    if not normalized or any(character.isspace() for character in raw):
        return False
    return (
        len(normalized) <= 5
        or any(character in normalized for character in ".^=-")
        or any(character.isdigit() for character in normalized)
    )


def deduplicate_ticker_options(options: Iterable[str]) -> list[str]:
    """Keep the first label for each ticker, preserving suggestion priority."""
    result = []
    seen = set()
    for option in options:
        ticker = option.split(" | ", maxsplit=1)[0]
        if ticker not in seen:
            seen.add(ticker)
            result.append(option)
    return result
