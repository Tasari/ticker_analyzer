from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any

from ticker_analyzer.ranking.universe import UNIVERSE_SCHEMA_VERSION, market_counts

SCORING_VERSION = 5
PROVIDER_SCHEMA_VERSION = "providers-v2"
METRIC_SCHEMA_VERSION = "metrics-v5"


def analyze_ticker(
    ticker_symbol: str,
    ranges: str | dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Load the heavy analysis engine only when a ranking worker needs it."""
    from ticker_analyzer.analysis.engine import analyze_ticker as execute

    return execute(ticker_symbol, ranges, config)


def config_digest(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_fingerprint(config: dict[str, Any], data_as_of: str) -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "config_version": int(config.get("version", 5)),
        "calibration_version": str(config.get("calibration_version", "v5.2-value-2026Q3")),
        "config_digest": config_digest(config),
        "provider_schema_version": PROVIDER_SCHEMA_VERSION,
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "peer_artifact_version": str(config.get("peer_artifact_version", "none")),
        "data_as_of": data_as_of,
    }


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
            "calibration_version": analysis.get("calibration_version", "v5.2-value-2026Q3"),
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
    checkpoint_every: int = 25,
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
                if checkpoint and completed % max(1, checkpoint_every) == 0:
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
            "universe": "Top US-listed equities plus up to 100 listings per supported XTB exchange",
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
        # The full universe is needed only to resume an incomplete checkpoint.
        # Completed rows already contain all display metadata, so retaining a
        # second copy wastes memory every time Streamlit loads the snapshot.
        "universe": universe if not complete else [],
    }
