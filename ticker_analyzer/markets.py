from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class MarketConvention:
    selector: str
    market: str
    country: str
    suffix: str
    currency: str
    timezone: str
    scanner: str | None = None


MARKETS: tuple[MarketConvention, ...] = (
    MarketConvention("United States / ADR", "US Exchanges", "United States", "", "USD", "America/New_York", "us"),
    MarketConvention("Poland (Warsaw)", "Warsaw Stock Exchange", "Poland", ".WA", "PLN", "Europe/Warsaw", "poland"),
    MarketConvention("United Kingdom (London)", "London Stock Exchange", "United Kingdom", ".L", "GBP", "Europe/London", "uk"),
    MarketConvention("Germany (Xetra)", "Xetra", "Germany", ".DE", "EUR", "Europe/Berlin", "germany"),
    MarketConvention("France (Paris)", "Euronext Paris", "France", ".PA", "EUR", "Europe/Paris", "france"),
    MarketConvention("Spain (Madrid)", "Bolsa de Madrid", "Spain", ".MC", "EUR", "Europe/Madrid", "spain"),
    MarketConvention("Italy (Milan)", "Borsa Italiana", "Italy", ".MI", "EUR", "Europe/Rome", "italy"),
    MarketConvention("Portugal (Lisbon)", "Euronext Lisbon", "Portugal", ".LS", "EUR", "Europe/Lisbon", "portugal"),
    MarketConvention("Netherlands (Amsterdam)", "Euronext Amsterdam", "Netherlands", ".AS", "EUR", "Europe/Amsterdam", "netherlands"),
    MarketConvention("Belgium (Brussels)", "Euronext Brussels", "Belgium", ".BR", "EUR", "Europe/Brussels", "belgium"),
    MarketConvention("Austria (Vienna)", "Vienna Stock Exchange", "Austria", ".VI", "EUR", "Europe/Vienna", "austria"),
    MarketConvention("Switzerland", "SIX Swiss Exchange", "Switzerland", ".SW", "CHF", "Europe/Zurich", "switzerland"),
    MarketConvention("Denmark (Copenhagen)", "Nasdaq Copenhagen", "Denmark", ".CO", "DKK", "Europe/Copenhagen", "denmark"),
    MarketConvention("Finland (Helsinki)", "Nasdaq Helsinki", "Finland", ".HE", "EUR", "Europe/Helsinki", "finland"),
    MarketConvention("Norway (Oslo)", "Oslo Bors", "Norway", ".OL", "NOK", "Europe/Oslo", "norway"),
    MarketConvention("Sweden (Stockholm)", "Nasdaq Stockholm", "Sweden", ".ST", "SEK", "Europe/Stockholm", "sweden"),
    MarketConvention("Hong Kong", "Hong Kong Stock Exchange", "Hong Kong", ".HK", "HKD", "Asia/Hong_Kong"),
    MarketConvention("Japan (Tokyo)", "Tokyo Stock Exchange", "Japan", ".T", "JPY", "Asia/Tokyo"),
    MarketConvention("Canada (Toronto)", "Toronto Stock Exchange", "Canada", ".TO", "CAD", "America/Toronto"),
    MarketConvention("Australia", "Australian Securities Exchange", "Australia", ".AX", "AUD", "Australia/Sydney"),
)

MARKET_SUFFIXES: dict[str, str] = {
    "Full Yahoo symbol": "",
    **{market.selector: market.suffix for market in MARKETS},
}
XTB_EXCHANGE_MARKETS: dict[str, tuple[str, str, str]] = {
    market.market: (market.scanner, market.country, market.suffix)
    for market in MARKETS
    if market.scanner and market.suffix
}

# Yahoo sometimes reports quotes in minor units while fundamentals are in the
# major currency. Exact spelling matters: GBP is pounds, GBp is pence.
MINOR_CURRENCIES: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 0.01),
    "GBX": ("GBP", 0.01),
    "ILA": ("ILS", 0.01),
    "ZAc": ("ZAR", 0.01),
    "ZAC": ("ZAR", 0.01),
    "USc": ("USD", 0.01),
}
PRICE_FIELDS = frozenset(
    {
        "currentPrice", "regularMarketPrice", "previousClose", "open", "dayLow", "dayHigh",
        "regularMarketOpen", "regularMarketDayLow", "regularMarketDayHigh",
        "fiftyTwoWeekLow", "fiftyTwoWeekHigh", "fiftyDayAverage", "twoHundredDayAverage",
        "targetLowPrice", "targetMeanPrice", "targetMedianPrice", "targetHighPrice",
    }
)
PRICE_COLUMNS = frozenset({"Open", "High", "Low", "Close", "Adj Close"})


def convention_for_ticker(ticker: Any) -> MarketConvention:
    symbol = str(ticker or "").strip().upper()
    matches = [market for market in MARKETS if market.suffix and symbol.endswith(market.suffix)]
    return max(matches, key=lambda market: len(market.suffix)) if matches else MARKETS[0]


def currency_convention(currency: Any) -> tuple[str, float]:
    raw = str(currency or "").strip()
    if raw in MINOR_CURRENCIES:
        return MINOR_CURRENCIES[raw]
    return raw.upper(), 1.0


def canonical_currency(currency: Any, ticker: Any = "") -> str:
    canonical, _ = currency_convention(currency)
    return canonical or convention_for_ticker(ticker).currency


def normalize_price_history(history: pd.DataFrame, currency: Any) -> pd.DataFrame:
    _, scale = currency_convention(currency)
    if history.empty or scale == 1:
        return history
    normalized = history.copy()
    for column in PRICE_COLUMNS.intersection(normalized.columns):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce") * scale
    return normalized


def normalize_quote_info(info: dict[str, Any], ticker: Any) -> dict[str, Any]:
    normalized = dict(info)
    already_normalized = bool(normalized.get("quoteValuesNormalized"))
    raw_currency = str(normalized.get("quoteCurrency") or normalized.get("currency") or "").strip()
    currency, scale = currency_convention(raw_currency)
    market = convention_for_ticker(ticker)
    normalized["quoteCurrency"] = raw_currency or currency or market.currency
    normalized["quoteUnitScale"] = scale
    normalized["currency"] = currency or market.currency
    for field in PRICE_FIELDS:
        value = normalized.get(field)
        if value is None or scale == 1 or already_normalized:
            continue
        try:
            normalized[field] = float(value) * scale
        except (TypeError, ValueError):
            pass
    normalized["quoteValuesNormalized"] = True
    provider_market = str(normalized.get("market") or "").strip()
    if provider_market and provider_market != market.market:
        normalized["providerMarket"] = provider_market
    normalized["market"] = market.market
    normalized["country"] = normalized.get("country") or market.country
    normalized["exchange"] = normalized.get("exchange") or market.market
    normalized["exchangeTimezoneName"] = normalized.get("exchangeTimezoneName") or market.timezone
    return normalized


def normalize_price_targets(targets: dict[str, Any], currency: Any) -> dict[str, Any]:
    _, scale = currency_convention(currency)
    if scale == 1:
        return targets
    normalized = dict(targets)
    price_keys = {"current", "low", "mean", "median", "high", *PRICE_FIELDS}
    for key, value in normalized.items():
        if key not in price_keys:
            continue
        try:
            normalized[key] = float(value) * scale
        except (TypeError, ValueError):
            pass
    return normalized
