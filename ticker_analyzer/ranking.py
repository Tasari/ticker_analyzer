from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

from ticker_analyzer.analysis.engine import analyze_ticker

DEFAULT_RANKING_PATH = Path("data/large_cap_ranking_v5.json")
SCORING_VERSION = 5
PROVIDER_SCHEMA_VERSION = "providers-v2"
METRIC_SCHEMA_VERSION = "metrics-v5"
UNIVERSE_SCHEMA_VERSION = "xtb-markets-v5"
US_MARKET = "United States"
CHINA_ADR_MARKET = "China (US ADR)"
CHINA_ADR_COUNTRIES = ("China", "Hong Kong")
XTB_EUROPE_MARKETS: dict[str, tuple[str, str, str]] = {
    "Poland": ("poland", "Poland", ".WA"),
    "United Kingdom": ("uk", "United Kingdom", ".L"),
    "Germany": ("germany", "Germany", ".DE"),
    "France": ("france", "France", ".PA"),
    "Spain": ("spain", "Spain", ".MC"),
    "Italy": ("italy", "Italy", ".MI"),
    "Portugal": ("portugal", "Portugal", ".LS"),
    "Netherlands": ("netherlands", "Netherlands", ".AS"),
    "Belgium": ("belgium", "Belgium", ".BR"),
    "Austria": ("austria", "Austria", ".VI"),
    "Switzerland": ("switzerland", "Switzerland", ".SW"),
    "Denmark": ("denmark", "Denmark", ".CO"),
    "Finland": ("finland", "Finland", ".HE"),
    "Norway": ("norway", "Norway", ".OL"),
    "Sweden": ("sweden", "Sweden", ".ST"),
}
TRADINGVIEW_COLUMNS = (
    "name",
    "description",
    "market_cap_basic",
    "exchange",
    "sector",
    "industry",
    "country",
    "type",
)


def config_digest(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_fingerprint(config: dict[str, Any], data_as_of: str) -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "config_version": int(config.get("version", 5)),
        "calibration_version": str(config.get("calibration_version", "v5.1-calibration-2026Q3")),
        "config_digest": config_digest(config),
        "provider_schema_version": PROVIDER_SCHEMA_VERSION,
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "peer_artifact_version": str(config.get("peer_artifact_version", "none")),
        "data_as_of": data_as_of,
    }


def normalize_ticker(ticker: Any) -> str:
    return str(ticker or "").strip().upper().replace("/", "-")


def fetch_large_cap_universe(
    limit: int = 1000,
    minimum_market_cap: float = 1_000_000_000,
    *,
    region: str = "us",
    country: str = US_MARKET,
    market: str = US_MARKET,
) -> list[dict[str, Any]]:
    query = yf.EquityQuery(
        "and",
        [
            yf.EquityQuery("eq", ["region", region]),
            yf.EquityQuery("gt", ["intradaymarketcap", minimum_market_cap]),
        ],
    )
    universe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offset in range(0, limit, 250):
        response = yf.screen(
            query,
            offset=offset,
            size=min(250, limit - offset),
            sortField="intradaymarketcap",
            sortAsc=False,
        )
        for quote in response.get("quotes", []):
            ticker = normalize_ticker(quote.get("symbol"))
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            universe.append(
                {
                    "ticker": ticker,
                    "company_name": quote.get("longName") or quote.get("shortName") or ticker,
                    "market_cap": quote.get("marketCap"),
                    "exchange": quote.get("fullExchangeName") or quote.get("exchange"),
                    "country": country,
                    "market": market,
                    "sector": quote.get("sector") or quote.get("sectorDisp"),
                    "industry": quote.get("industry") or quote.get("industryDisp"),
                    "universe_source": "Yahoo Finance equity screener",
                }
            )
    return universe[:limit]


def yahoo_ticker_from_tradingview(symbol: Any, yahoo_suffix: str) -> str:
    local_symbol = str(symbol or "").rsplit(":", 1)[-1]
    local_symbol = local_symbol.replace("/", "-").replace(".", "-").replace("_", "-")
    ticker = normalize_ticker(local_symbol)
    return f"{ticker}{yahoo_suffix}" if ticker else ""


def fetch_tradingview_market_universe(
    limit: int,
    *,
    scanner_market: str,
    country: str,
    market: str,
    yahoo_suffix: str,
    attempts: int = 3,
    retry_delay: float = 2.0,
) -> list[dict[str, Any]]:
    """Fetch one European market without relying on Yahoo's rate-limited screener."""
    payload = {
        "markets": [scanner_market],
        "symbols": {"query": {"types": []}, "tickers": []},
        "options": {"lang": "en"},
        "columns": list(TRADINGVIEW_COLUMNS),
        "filter": [
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc", "nullsFirst": False},
        "range": [0, limit],
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = requests.post(
                f"https://scanner.tradingview.com/{scanner_market}/scan",
                json=payload,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
            universe = []
            for row in response.json().get("data", []) or []:
                values = dict(zip(TRADINGVIEW_COLUMNS, row.get("d", []), strict=False))
                ticker = yahoo_ticker_from_tradingview(row.get("s") or values.get("name"), yahoo_suffix)
                if not ticker:
                    continue
                universe.append(
                    {
                        "ticker": ticker,
                        "company_name": values.get("description") or values.get("name") or ticker,
                        "market_cap": values.get("market_cap_basic"),
                        "exchange": values.get("exchange") or str(row.get("s") or "").partition(":")[0],
                        "country": values.get("country") or country,
                        "market": market,
                        "sector": values.get("sector"),
                        "industry": values.get("industry"),
                        "universe_source": "TradingView stock screener",
                    }
                )
            if universe:
                return universe[:limit]
            last_error = RuntimeError("TradingView returned no companies")
        except Exception as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(retry_delay * (attempt + 1))
    raise RuntimeError(f"TradingView {market} universe unavailable after {attempts} attempts: {last_error}")


def fetch_large_cap_universe_nasdaq(limit: int = 10000) -> list[dict[str, Any]]:
    response = requests.get(
        "https://api.nasdaq.com/api/screener/stocks",
        params={"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"},
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("rows", []) or []
    universe = []
    for row in rows:
        ticker = normalize_ticker(row.get("symbol"))
        try:
            market_cap = float(row.get("marketCap") or 0)
        except (TypeError, ValueError):
            market_cap = 0.0
        if not ticker or market_cap <= 0:
            continue
        universe.append(
            {
                "ticker": ticker,
                "company_name": row.get("name") or ticker,
                "market_cap": market_cap,
                "exchange": None,
                "sector": row.get("sector"),
                "industry": row.get("industry"),
                "country": row.get("country"),
                "market": US_MARKET,
                "universe_source": "Nasdaq stock screener",
            }
        )
    return sorted(universe, key=lambda item: item["market_cap"], reverse=True)[:limit]


def select_nasdaq_market(
    universe: list[dict[str, Any]],
    *,
    country: str | Iterable[str],
    market: str,
    limit: int,
) -> list[dict[str, Any]]:
    selected = []
    countries = (country,) if isinstance(country, str) else tuple(country)
    expected_countries = {value.casefold() for value in countries}
    for source_item in universe:
        source_country = str(source_item.get("country") or "").strip()
        if source_country.casefold() not in expected_countries:
            continue
        selected.append({**source_item, "country": source_country, "market": market})
        if len(selected) >= limit:
            break
    return selected


def combine_market_universes(
    us_nasdaq: list[dict[str, Any]],
    us_yahoo: list[dict[str, Any]],
    china_adrs: list[dict[str, Any]],
    regional_universes: list[list[dict[str, Any]]],
    *,
    us_limit: int,
    market_limit: int,
) -> list[dict[str, Any]]:
    yahoo_by_ticker = {item["ticker"]: item for item in us_yahoo}

    def enrich(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = []
        for source_item in items:
            item = dict(source_item)
            supplement = yahoo_by_ticker.get(item["ticker"], {})
            for key, value in supplement.items():
                if item.get(key) in (None, "") and value not in (None, ""):
                    item[key] = value
            item["exchange"] = item.get("exchange") or "US-listed"
            enriched.append(item)
        return enriched

    us = enrich(us_nasdaq)
    seen_us = {item["ticker"] for item in us}
    for item in us_yahoo:
        if len(us) >= us_limit:
            break
        if item["ticker"] not in seen_us:
            seen_us.add(item["ticker"])
            us.append(item)
    buckets = [
        us[:us_limit],
        enrich(china_adrs[:market_limit]),
        *(items[:market_limit] for items in regional_universes),
    ]
    combined: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in buckets:
        for item in bucket:
            ticker = normalize_ticker(item.get("ticker"))
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            combined.append({**item, "ticker": ticker})
    return combined


def merge_large_cap_universes(
    *universes: list[dict[str, Any]], limit: int = 1000
) -> list[dict[str, Any]]:
    """Combine screener results without losing US-listed foreign companies/ADRs."""
    combined: dict[str, dict[str, Any]] = {}
    for universe in universes:
        for source_item in universe:
            ticker = normalize_ticker(source_item.get("ticker"))
            if not ticker:
                continue
            item = {**source_item, "ticker": ticker}
            existing = combined.get(ticker)
            if existing is None:
                combined[ticker] = item
                continue
            for key, value in item.items():
                if existing.get(key) in (None, "") and value not in (None, ""):
                    existing[key] = value
            existing["market_cap"] = max(
                float(existing.get("market_cap") or 0),
                float(item.get("market_cap") or 0),
            )

    return sorted(
        combined.values(),
        key=lambda item: float(item.get("market_cap") or 0),
        reverse=True,
    )[:limit]


def market_counts(universe: list[dict[str, Any]]) -> dict[str, int]:
    return {
        market: sum((item.get("market") or "Unknown") == market for item in universe)
        for market in sorted({str(item.get("market") or "Unknown") for item in universe})
    }


def validate_market_coverage(
    universe: list[dict[str, Any]],
    required_markets: Iterable[str],
) -> dict[str, int]:
    counts = market_counts(universe)
    missing = [market for market in required_markets if counts.get(market, 0) == 0]
    if missing:
        raise RuntimeError(f"Ranking universe is missing markets: {', '.join(missing)}")
    return counts


def checkpoint_universe_is_current(
    payload: dict[str, Any] | None,
    *,
    limit: int,
    required_markets: Iterable[str] = (),
) -> bool:
    if not payload:
        return False
    metadata = payload.get("metadata", {})
    universe = payload.get("universe", [])
    required = tuple(required_markets)
    has_required_coverage = len(universe) >= limit
    if required:
        counts = market_counts(universe)
        has_required_coverage = (
            counts.get(US_MARKET, 0) >= limit
            and all(counts.get(market, 0) > 0 for market in required)
        )
    return (
        not metadata.get("complete", False)
        and metadata.get("universe_schema_version") == UNIVERSE_SCHEMA_VERSION
        and has_required_coverage
    )


def ranking_row(
    universe_item: dict[str, Any], analysis: dict[str, Any], fingerprint: dict[str, Any] | None = None
) -> dict[str, Any]:
    tabs = analysis.get("tabs", {})
    return {
        **universe_item,
        "company_name": analysis.get("company_name") or universe_item.get("company_name"),
        "profile": analysis.get("profile"),
        "overall_score": analysis.get("overall_score"),
        "rating": analysis.get("rating"),
        "rating_code": analysis.get("rating_code"),
        "rating_confidence": analysis.get("rating_confidence"),
        "rating_status": analysis.get("rating_status"),
        "rating_caps": analysis.get("rating_caps", []),
        "rating_reason_codes": analysis.get("rating_reason_codes", []),
        "data_quality": analysis.get("data_quality", analysis.get("confidence")),
        "model_applicability": analysis.get("model_applicability"),
        "warnings": analysis.get("warnings", []),
        "growth_score": tabs.get("Growth", {}).get("score"),
        "fundamentals_score": tabs.get("Fundamentals", {}).get("score"),
        "value_score": tabs.get("Value", {}).get("score"),
        "growth_coverage": tabs.get("Growth", {}).get("coverage", {}).get("percentage"),
        "fundamentals_coverage": tabs.get("Fundamentals", {}).get("coverage", {}).get("percentage"),
        "value_coverage": tabs.get("Value", {}).get("coverage", {}).get("percentage"),
        **(fingerprint or {
            "scoring_version": analysis.get("scoring_version", SCORING_VERSION),
            "config_version": analysis.get("config_version", 5),
            "calibration_version": analysis.get("calibration_version", "v5.1-calibration-2026Q3"),
        }),
    }


def sort_ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("overall_score") is not None,
            float(row.get("overall_score") or -1),
            float(row.get("data_quality", row.get("confidence")) or -1),
            float(row.get("market_cap") or -1),
        ),
        reverse=True,
    )
    for position, row in enumerate(ordered, start=1):
        row["rank"] = position if row.get("overall_score") is not None else None
    return ordered


def build_large_cap_ranking(
    universe: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    ranges: str = "3Y",
    workers: int = 5,
    existing: dict[str, Any] | None = None,
    analyzer: Callable[[str, str | dict[str, str], dict[str, Any]], dict[str, Any]] = analyze_ticker,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    retries: int = 2,
    retry_delay: float = 5.0,
    retry_insufficient: bool = False,
    data_as_of: str | None = None,
) -> dict[str, Any]:
    universe_tickers = {item["ticker"] for item in universe}
    as_of = data_as_of or datetime.now(UTC).date().isoformat()
    fingerprint = analysis_fingerprint(config, as_of)
    existing_metadata = (existing or {}).get("metadata", {})
    compatible_checkpoint = all(existing_metadata.get(key) == value for key, value in fingerprint.items())
    previous_rows = {
        row["ticker"]: row
        for row in (existing or {}).get("companies", [])
        if compatible_checkpoint
        and row.get("ticker") in universe_tickers
        and all(row.get(key) == value for key, value in fingerprint.items())
        and (not retry_insufficient or row.get("overall_score") is not None)
    }
    error_by_ticker = {
        item["ticker"]: item
        for item in (existing or {}).get("errors", [])
        if compatible_checkpoint
        and item.get("ticker") in universe_tickers
        and item.get("ticker") not in previous_rows
    }
    pending = [item for item in universe if item["ticker"] not in previous_rows]
    rows = list(previous_rows.values())

    def analyze(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                analysis_ranges = {
                    "Growth": ranges,
                    "Fundamentals": ranges,
                    "Value": ranges,
                    "_data_as_of": as_of,
                }
                return item, analyzer(item["ticker"], analysis_ranges, config)
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_delay * (attempt + 1))
        raise last_error or RuntimeError("analysis failed")

    worker_count = max(1, min(workers, 8))
    pending_items = iter(pending)
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        # Keep only a bounded number of futures alive. Keeping one future for
        # every company retains each completed full analysis until the entire
        # run ends, which can exhaust a small Streamlit Cloud container.
        futures = {}
        for _ in range(worker_count):
            try:
                item = next(pending_items)
            except StopIteration:
                break
            futures[executor.submit(analyze, item)] = item

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                item = futures.pop(future)
                completed += 1
                try:
                    source, analysis = future.result()
                    rows.append(ranking_row(source, analysis, fingerprint))
                    error_by_ticker.pop(item["ticker"], None)
                except Exception as exc:
                    error_by_ticker[item["ticker"]] = {"ticker": item["ticker"], "error": str(exc)}
                if checkpoint and completed % 10 == 0:
                    checkpoint(
                        ranking_payload(
                            universe, rows, list(error_by_ticker.values()), ranges, config,
                            complete=False, data_as_of=as_of,
                            processed_count=len(previous_rows) + completed,
                        )
                    )
                try:
                    next_item = next(pending_items)
                except StopIteration:
                    continue
                futures[executor.submit(analyze, next_item)] = next_item
    return ranking_payload(
        universe, rows, list(error_by_ticker.values()), ranges, config,
        complete=True, data_as_of=as_of, processed_count=len(universe),
    )


def ranking_payload(
    universe: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    errors: list[dict[str, str]],
    ranges: str,
    config: dict[str, Any] | None = None,
    *,
    complete: bool,
    data_as_of: str | None = None,
    processed_count: int | None = None,
) -> dict[str, Any]:
    ranked = sort_ranking(rows)
    unique_errors = sorted({item["ticker"]: item for item in errors}.values(), key=lambda item: item["ticker"])
    as_of = data_as_of or datetime.now(UTC).date().isoformat()
    fingerprint = analysis_fingerprint(config or {}, as_of)
    return {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "universe": "USA 1000 plus up to 100 companies per supported international market",
            "market_counts": market_counts(universe),
            "requested": len(universe),
            "processed": len(rows) + len(unique_errors) if processed_count is None else processed_count,
            "analyzed": len(rows),
            "scored": sum(row.get("overall_score") is not None for row in rows),
            "insufficient_data": sum(row.get("overall_score") is None for row in rows),
            "failed": len(unique_errors),
            "ranges": ranges,
            "universe_schema_version": UNIVERSE_SCHEMA_VERSION,
            **fingerprint,
            "code_commit": os.getenv("GIT_COMMIT", "unknown"),
            "complete": complete,
        },
        "companies": ranked,
        "errors": unique_errors,
        "universe": universe,
    }


def load_ranking(path: Path = DEFAULT_RANKING_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"metadata": {}, "companies": [], "errors": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_ranking(payload: dict[str, Any], path: Path = DEFAULT_RANKING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
