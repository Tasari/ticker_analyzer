from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from ticker_analyzer.ranking_storage import save_ranking
from ticker_analyzer.ranking_universe import XTB_EXCHANGE_MARKETS, yahoo_ticker_from_tradingview

ETF_RANKING_PATH = Path("data/etf_ranking_v1.json")
CRYPTO_RANKING_PATH = Path("data/crypto_ranking_v1.json")
ETF_COLUMNS = (
    "name",
    "description",
    "close",
    "currency",
    "exchange",
    "country",
    "Perf.1M",
    "Perf.3M",
    "Perf.6M",
    "Perf.Y",
    "Volatility.M",
    "Value.Traded",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def fetch_etfs_for_exchange(
    limit: int,
    *,
    scanner_market: str,
    country: str,
    market: str,
    yahoo_suffix: str,
    request: Callable[..., Any] = requests.post,
) -> list[dict[str, Any]]:
    payload = {
        "markets": [scanner_market],
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "en"},
        "columns": list(ETF_COLUMNS),
        "filter": [{"left": "type", "operation": "equal", "right": "fund"}],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc", "nullsFirst": False},
        "range": [0, limit],
    }
    response = request(
        f"https://scanner.tradingview.com/{scanner_market}/scan",
        json=payload,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    rows = []
    for source in response.json().get("data", []) or []:
        values = dict(zip(ETF_COLUMNS, source.get("d", []), strict=False))
        ticker = yahoo_ticker_from_tradingview(source.get("s") or values.get("name"), yahoo_suffix)
        if not ticker:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": values.get("description") or values.get("name") or ticker,
                "exchange": values.get("exchange") or str(source.get("s") or "").partition(":")[0],
                "country": values.get("country") or country,
                "market": market,
                "price": _number(values.get("close")),
                "currency": values.get("currency"),
                "return_1m": _number(values.get("Perf.1M")),
                "return_3m": _number(values.get("Perf.3M")),
                "return_6m": _number(values.get("Perf.6M")),
                "return_1y": _number(values.get("Perf.Y")),
                "volatility_1m": _number(values.get("Volatility.M")),
                "traded_value": _number(values.get("Value.Traded")),
            }
        )
    return rows[:limit]


def fetch_crypto_market(
    limit: int = 100,
    *,
    request: Callable[..., Any] = requests.get,
) -> list[dict[str, Any]]:
    response = request(
        "https://api.coingecko.com/api/v3/coins/markets",
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": min(250, max(1, limit)),
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "7d,30d,200d,1y",
        },
        headers={"User-Agent": "ticker-analyzer/1.0", "Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in response.json() or []:
        symbol = str(source.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        ticker = f"{symbol}-USD"
        if ticker in seen:
            ticker = f"{symbol}-{str(source.get('id') or '').upper()}-USD"
        seen.add(ticker)
        market_cap = _number(source.get("market_cap"))
        volume = _number(source.get("total_volume"))
        result.append(
            {
                "ticker": ticker,
                "coin_id": source.get("id"),
                "name": source.get("name") or symbol,
                "symbol": symbol,
                "price": _number(source.get("current_price")),
                "market_cap": market_cap,
                "market_cap_rank": source.get("market_cap_rank"),
                "total_volume": volume,
                "volume_market_cap": volume / market_cap if volume is not None and market_cap else None,
                "return_24h": _number(source.get("price_change_percentage_24h")),
                "return_7d": _number(source.get("price_change_percentage_7d_in_currency")),
                "return_30d": _number(source.get("price_change_percentage_30d_in_currency")),
                "return_200d": _number(source.get("price_change_percentage_200d_in_currency")),
                "return_1y": _number(source.get("price_change_percentage_1y_in_currency")),
                "ath_drawdown": _number(source.get("ath_change_percentage")),
            }
        )
    return result[:limit]


def _percentiles(rows: list[dict[str, Any]], field: str, *, lower_is_better: bool = False) -> dict[int, float]:
    available = [(index, _number(row.get(field))) for index, row in enumerate(rows)]
    available = [(index, value) for index, value in available if value is not None]
    if not available:
        return {}
    ordered = sorted(available, key=lambda pair: pair[1], reverse=lower_is_better)
    denominator = max(1, len(ordered) - 1)
    return {index: position / denominator * 100 for position, (index, _value) in enumerate(ordered)}


def score_market_rows(
    rows: list[dict[str, Any]],
    weights: dict[str, float],
    *,
    lower_is_better: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    percentiles = {
        field: _percentiles(rows, field, lower_is_better=field in lower_is_better) for field in weights
    }
    scored = []
    for index, source in enumerate(rows):
        parts = [(percentiles[field][index], weight) for field, weight in weights.items() if index in percentiles[field]]
        row = dict(source)
        coverage = sum(weight for _value, weight in parts) / sum(weights.values()) * 100
        score = sum(value * weight for value, weight in parts) / sum(weight for _value, weight in parts) if parts else None
        row["overall_score"] = round(score, 1) if score is not None and coverage >= 50 else None
        row["data_coverage"] = round(coverage, 1)
        row["rating"] = _market_rating(row["overall_score"])
        scored.append(row)
    scored.sort(
        key=lambda item: (item["overall_score"] is not None, item["overall_score"] or -1),
        reverse=True,
    )
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank if row["overall_score"] is not None else None
    return scored


def _market_rating(score: float | None) -> str:
    if score is None:
        return "Insufficient data"
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Positive"
    if score >= 40:
        return "Neutral"
    if score >= 20:
        return "Weak"
    return "Very weak"


def _payload(asset_class: str, rows: list[dict[str, Any]], errors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "asset_class": asset_class,
            "requested": len(rows) + len(errors),
            "analyzed": len(rows),
            "scored": sum(row.get("overall_score") is not None for row in rows),
            "failed": len(errors),
            "complete": True,
            "scoring_model": "cross-sectional market performance, risk and liquidity v1",
        },
        "companies": rows,
        "errors": errors,
    }


def build_etf_ranking(limit_per_exchange: int = 50) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(
                fetch_etfs_for_exchange,
                limit_per_exchange,
                scanner_market=scanner,
                country=country,
                market=market,
                yahoo_suffix=suffix,
            ): market
            for market, (scanner, country, suffix) in XTB_EXCHANGE_MARKETS.items()
        }
        for future in as_completed(futures):
            market = futures[future]
            try:
                rows.extend(future.result())
            except Exception as exc:
                errors.append({"ticker": market, "error": str(exc)})
    unique = {row["ticker"]: row for row in rows}
    ranked = score_market_rows(
        list(unique.values()),
        {
            "return_1m": 5,
            "return_3m": 15,
            "return_6m": 20,
            "return_1y": 30,
            "volatility_1m": 15,
            "traded_value": 15,
        },
        lower_is_better=frozenset({"volatility_1m"}),
    )
    return _payload("ETF", ranked, errors)


def build_crypto_ranking(limit: int = 100) -> dict[str, Any]:
    rows = fetch_crypto_market(limit)
    ranked = score_market_rows(
        rows,
        {
            "return_7d": 5,
            "return_30d": 15,
            "return_200d": 20,
            "return_1y": 25,
            "volume_market_cap": 15,
            "market_cap": 10,
            "ath_drawdown": 10,
        },
    )
    return _payload("Crypto", ranked, [])


def refresh_etf_ranking() -> dict[str, Any]:
    payload = build_etf_ranking()
    if not payload["companies"]:
        raise RuntimeError("ETF providers returned no instruments; the previous snapshot was retained.")
    save_ranking(payload, ETF_RANKING_PATH)
    return payload


def refresh_crypto_ranking() -> dict[str, Any]:
    payload = build_crypto_ranking()
    if not payload["companies"]:
        raise RuntimeError("CoinGecko returned no cryptocurrencies; the previous snapshot was retained.")
    save_ranking(payload, CRYPTO_RANKING_PATH)
    return payload
