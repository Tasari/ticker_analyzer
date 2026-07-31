from __future__ import annotations

import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import streamlit as st
import yfinance as yf

from ticker_analyzer import analyze_ticker
from ticker_analyzer.ranking import DEFAULT_RANKING_PATH, load_ranking

logger = logging.getLogger(__name__)
MAX_ANALYSIS_WORKERS = 5
AnalysisResult = dict[str, Any]
AnalysisError = str
TickerAnalysisOutcome = tuple[AnalysisResult | None, AnalysisError | None]


def refresh_large_cap_ranking(
    output_path: Path = DEFAULT_RANKING_PATH,
    *,
    limit: int = 1000,
    workers: int = 5,
    timeout: int = 1200,
) -> tuple[bool, str, dict[str, Any]]:
    if os.getenv("APP_MODE", "local").strip().lower() == "production" and os.getenv(
        "ALLOW_RANKING_REFRESH", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "Ranking refresh is disabled in production.", {}
    project_root = Path(__file__).resolve().parents[2]
    resolved_output = output_path if output_path.is_absolute() else project_root / output_path
    refresh_path = resolved_output.with_suffix(".refresh.json")
    lock_path = resolved_output.with_suffix(".refresh.lock")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return False, "A ranking update is already running.", {}

    command = [
        sys.executable,
        "-m",
        "scripts.build_large_cap_ranking",
        "--limit",
        str(limit),
        "--workers",
        str(workers),
        "--ranges",
        "3Y",
        "--output",
        str(refresh_path),
        "--public-fallback",
        "--retry-insufficient",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            command,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=creationflags,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Unknown generator error").strip().splitlines()[-1]
            return False, f"Ranking update failed: {detail}", {}
        payload = load_ranking(refresh_path)
        metadata = payload.get("metadata", {})
        if not metadata.get("complete") or len(payload.get("companies", [])) != limit:
            return False, "Ranking update stopped before all companies were processed; the checkpoint was preserved.", metadata
        refresh_path.replace(resolved_output)
        return True, f"Ranking updated: {metadata.get('scored', 0)} scored, {metadata.get('insufficient_data', 0)} insufficient data.", metadata
    except subprocess.TimeoutExpired:
        return False, "Ranking update timed out; the checkpoint was preserved and the next run will resume it.", {}
    finally:
        lock_path.unlink(missing_ok=True)


def analyze_selected_tickers(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    if len(tickers) <= 1:
        return analyze_tickers_sequentially(tickers, ranges, config)

    completed: dict[str, TickerAnalysisOutcome] = {}
    with ThreadPoolExecutor(max_workers=analysis_worker_count(tickers)) as executor:
        futures = {
            executor.submit(analyze_one_ticker, ticker, ranges, config): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            completed[ticker] = future.result()
    return ordered_analysis_results(tickers, completed)


def analysis_worker_count(tickers: list[str]) -> int:
    return min(MAX_ANALYSIS_WORKERS, max(1, len(tickers)))


def analyze_tickers_sequentially(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    completed = {
        ticker: analyze_one_ticker(ticker, ranges, config)
        for ticker in tickers
    }
    return ordered_analysis_results(tickers, completed)


def analyze_one_ticker(ticker: str, ranges: dict[str, str], config: dict) -> TickerAnalysisOutcome:
    try:
        return analyze_ticker(ticker, ranges, config), None
    except ValueError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("Unexpected analysis failure for %s", ticker)
        return None, "Unexpected internal error. Check application logs."


def ordered_analysis_results(
    tickers: list[str],
    completed: dict[str, TickerAnalysisOutcome],
) -> tuple[dict, dict]:
    results = {}
    errors = {}
    for ticker in tickers:
        result, error = completed.get(ticker, (None, "Analysis did not complete."))
        if error is not None:
            errors[ticker] = error
        elif result is not None:
            results[ticker] = result
    return results, errors


@st.cache_data(ttl=900, show_spinner=False)
def search_tickers(searchterm: str) -> list[str]:
    query = searchterm.strip()
    if len(query) < 1:
        return []
    try:
        results = yf.Search(
            query,
            max_results=10,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            enable_fuzzy_query=True,
            recommended=0,
        ).quotes
    except Exception:
        logger.warning("Ticker search failed for query %s", query, exc_info=True)
        return []

    suggestions = []
    for result in results:
        if result.get("quoteType") != "EQUITY":
            continue
        symbol = result.get("symbol")
        if not symbol:
            continue
        name = result.get("longname") or result.get("shortname") or symbol
        exchange = result.get("exchDisp") or result.get("exchange") or ""
        suggestions.append(f"{symbol} | {name} | {exchange}".rstrip(" |"))
    return suggestions
