from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

from ticker_analyzer.analysis.engine import analyze_ticker

DEFAULT_RANKING_PATH = Path("data/large_cap_ranking_v4.json")


def normalize_ticker(ticker: Any) -> str:
    return str(ticker or "").strip().upper().replace("/", "-")


def fetch_large_cap_universe(limit: int = 1000, minimum_market_cap: float = 1_000_000_000) -> list[dict[str, Any]]:
    query = yf.EquityQuery(
        "and",
        [
            yf.EquityQuery("eq", ["region", "us"]),
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
                    "exchange": quote.get("exchange"),
                    "sector": quote.get("sector") or quote.get("sectorDisp"),
                    "industry": quote.get("industry") or quote.get("industryDisp"),
                    "universe_source": "Yahoo Finance equity screener",
                }
            )
    return universe[:limit]


def fetch_large_cap_universe_nasdaq(limit: int = 1000) -> list[dict[str, Any]]:
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
                "universe_source": "Nasdaq stock screener",
            }
        )
    return sorted(universe, key=lambda item: item["market_cap"], reverse=True)[:limit]


def ranking_row(universe_item: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    tabs = analysis.get("tabs", {})
    return {
        **universe_item,
        "company_name": analysis.get("company_name") or universe_item.get("company_name"),
        "profile": analysis.get("profile"),
        "overall_score": analysis.get("overall_score"),
        "rating": analysis.get("rating"),
        "data_quality": analysis.get("data_quality", analysis.get("confidence")),
        "confidence": analysis.get("data_quality", analysis.get("confidence")),
        "growth_score": tabs.get("Growth", {}).get("score"),
        "fundamentals_score": tabs.get("Fundamentals", {}).get("score"),
        "value_score": tabs.get("Value", {}).get("score"),
        "growth_coverage": tabs.get("Growth", {}).get("coverage", {}).get("percentage"),
        "fundamentals_coverage": tabs.get("Fundamentals", {}).get("coverage", {}).get("percentage"),
        "value_coverage": tabs.get("Value", {}).get("coverage", {}).get("percentage"),
        "scoring_version": analysis.get("scoring_version", 4),
        "config_version": analysis.get("config_version", 4),
        "calibration_version": analysis.get("calibration_version", "v4-bootstrap-2026Q3"),
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
) -> dict[str, Any]:
    universe_tickers = {item["ticker"] for item in universe}
    expected_config_version = int(config.get("version", 4))
    expected_calibration = str(config.get("calibration_version", "v4-bootstrap-2026Q3"))
    previous_rows = {
        row["ticker"]: row
        for row in (existing or {}).get("companies", [])
        if row.get("ticker") in universe_tickers
        and int(row.get("scoring_version", 0)) == 4
        and int(row.get("config_version", 0)) == expected_config_version
        and str(row.get("calibration_version", "")) == expected_calibration
        and (not retry_insufficient or row.get("overall_score") is not None)
    }
    error_by_ticker = {
        item["ticker"]: item
        for item in (existing or {}).get("errors", [])
        if item.get("ticker") in universe_tickers and item.get("ticker") not in previous_rows
    }
    pending = [item for item in universe if item["ticker"] not in previous_rows]
    rows = list(previous_rows.values())

    def analyze(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return item, analyzer(item["ticker"], ranges, config)
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(retry_delay * (attempt + 1))
        raise last_error or RuntimeError("analysis failed")

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = {executor.submit(analyze, item): item for item in pending}
        for completed, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            try:
                source, analysis = future.result()
                rows.append(ranking_row(source, analysis))
                error_by_ticker.pop(item["ticker"], None)
            except Exception as exc:
                error_by_ticker[item["ticker"]] = {"ticker": item["ticker"], "error": str(exc)}
            if checkpoint and completed % 10 == 0:
                checkpoint(ranking_payload(universe, rows, list(error_by_ticker.values()), ranges, config, complete=False))
    return ranking_payload(universe, rows, list(error_by_ticker.values()), ranges, config, complete=True)


def ranking_payload(
    universe: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    errors: list[dict[str, str]],
    ranges: str,
    config: dict[str, Any] | None = None,
    *,
    complete: bool,
) -> dict[str, Any]:
    ranked = sort_ranking(rows)
    unique_errors = sorted({item["ticker"]: item for item in errors}.values(), key=lambda item: item["ticker"])
    return {
        "metadata": {
            "generated_at": datetime.now(UTC).isoformat(),
            "universe": (
                f"largest listed equities by {universe[0].get('universe_source', 'market cap screener')} market cap"
                if universe
                else "large-cap equities"
            ),
            "requested": len(universe),
            "analyzed": len(rows),
            "scored": sum(row.get("overall_score") is not None for row in rows),
            "insufficient_data": sum(row.get("overall_score") is None for row in rows),
            "failed": len(unique_errors),
            "ranges": ranges,
            "scoring_version": 4,
            "config_version": int((config or {}).get("version", 4)),
            "calibration_version": str((config or {}).get("calibration_version", "v4-bootstrap-2026Q3")),
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
