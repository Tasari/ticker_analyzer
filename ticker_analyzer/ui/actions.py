from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import streamlit as st

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
    market_limit: int = 100,
    workers: int = 3,
    timeout: int = 3600,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    if os.getenv("APP_MODE", "local").strip().lower() == "production" and os.getenv(
        "ALLOW_RANKING_REFRESH", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "Ranking refresh is disabled in production.", {}
    project_root = Path(__file__).resolve().parents[2]
    resolved_output = output_path if output_path.is_absolute() else project_root / output_path
    refresh_path = resolved_output.with_suffix(".refresh.json")
    lock_path = resolved_output.with_suffix(".refresh.lock")
    log_path = resolved_output.with_suffix(".refresh.log")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if not acquire_refresh_lock(lock_path):
        return False, "A ranking update is already running.", {}

    command = [
        sys.executable,
        "-m",
        "scripts.build_large_cap_ranking",
        "--limit",
        str(limit),
        "--workers",
        str(workers),
        "--market-limit",
        str(market_limit),
        "--ranges",
        "3Y",
        "--output",
        str(refresh_path),
        "--public-fallback",
        "--retry-insufficient",
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            run_kwargs = {
                "cwd": project_root,
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
                "text": True,
                "timeout": timeout,
                "check": False,
                "creationflags": creationflags,
            }
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(subprocess.run, command, **run_kwargs)
                last_progress: tuple[int, int, int] | None = None
                while not future.done():
                    if progress_callback and refresh_path.exists():
                        progress = load_ranking(refresh_path).get("metadata", {})
                        marker = (
                            int(progress.get("processed", progress.get("analyzed", 0)) or 0),
                            int(progress.get("failed", 0) or 0),
                            int(progress.get("requested", 0) or 0),
                        )
                        if marker != last_progress:
                            progress_callback(progress)
                            last_progress = marker
                    time.sleep(0.5)
                completed = future.result()
        if completed.returncode != 0:
            detail = read_log_tail(log_path) or "Unknown generator error"
            return False, f"Ranking update failed: {detail}", {}
        payload = load_ranking(refresh_path)
        metadata = payload.get("metadata", {})
        if progress_callback:
            progress_callback(metadata)
        if not ranking_refresh_is_complete(payload, expected_limit=limit):
            return False, "Ranking update stopped before all companies were processed; the checkpoint was preserved.", metadata
        refresh_path.replace(resolved_output)
        return True, (
            f"Ranking updated: {metadata.get('scored', 0)} scored, "
            f"{metadata.get('insufficient_data', 0)} insufficient data, "
            f"{metadata.get('failed', 0)} failed."
        ), metadata
    except subprocess.TimeoutExpired:
        return False, "Ranking update timed out; the checkpoint was preserved and the next run will resume it.", {}
    finally:
        lock_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)


def acquire_refresh_lock(lock_path: Path) -> bool:
    for attempt in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            return True
        except FileExistsError:
            if attempt == 0 and refresh_lock_is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            return False
    return False


def refresh_lock_is_stale(lock_path: Path) -> bool:
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return True
    return False


def read_log_tail(path: Path, max_bytes: int = 16_384) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().decode("utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


def ranking_refresh_is_complete(payload: dict[str, Any], *, expected_limit: int) -> bool:
    metadata = payload.get("metadata", {})
    if not metadata.get("complete"):
        return False
    requested = int(metadata.get("requested", expected_limit) or 0)
    processed = len(payload.get("companies", [])) + len(payload.get("errors", []))
    return requested > 0 and processed >= requested


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
        return cached_ticker_analysis(ticker, ranges, config, id(analyze_ticker)), None
    except ValueError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("Unexpected analysis failure for %s", ticker)
        return None, "Unexpected internal error. Check application logs."


@st.cache_data(ttl=900, max_entries=250, show_spinner=False)
def cached_ticker_analysis(
    ticker: str,
    ranges: dict[str, str],
    config: dict,
    analyzer_identity: int,
) -> AnalysisResult:
    """Cache successful analyses independently so expanding a comparison is incremental."""
    del analyzer_identity  # Included only to isolate patched/test analyzer implementations.
    return analyze_ticker(ticker, ranges, config)


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
    import yfinance as yf

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
