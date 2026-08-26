from __future__ import annotations

from collections import Counter
from typing import Any

from ticker_analyzer.numbers import clean_number


def build_ranking_quality_report(
    current: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = current.get("metadata", {})
    companies = current.get("companies", [])
    errors = current.get("errors", [])
    expected_markets = metadata.get("market_counts", {})
    analyzed_by_market = Counter(str(row.get("market") or "Unknown") for row in companies)
    scored_by_market = Counter(
        str(row.get("market") or "Unknown")
        for row in companies
        if row.get("overall_score") is not None
    )
    markets = []
    for market in sorted(set(expected_markets) | set(analyzed_by_market)):
        expected = int(expected_markets.get(market, 0) or 0)
        analyzed = analyzed_by_market.get(market, 0)
        markets.append(
            {
                "market": market,
                "expected": expected,
                "analyzed": analyzed,
                "scored": scored_by_market.get(market, 0),
                "coverage": analyzed / expected if expected else None,
            }
        )

    requested = int(metadata.get("requested", 0) or 0)
    processed = int(metadata.get("processed", len(companies) + len(errors)) or 0)
    scored = sum(row.get("overall_score") is not None for row in companies)
    error_categories = Counter(_error_category(str(item.get("error", ""))) for item in errors)
    warnings = []
    if not metadata.get("complete"):
        warnings.append("Snapshot is marked incomplete.")
    if requested and processed < requested:
        warnings.append(f"Only {processed:,} of {requested:,} requested companies were processed.")
    if requested and len(errors) / requested > 0.1:
        warnings.append(f"Provider failures affected {len(errors) / requested:.1%} of the universe.")
    if companies and scored / len(companies) < 0.7:
        warnings.append(f"Only {scored / len(companies):.1%} of analyzed companies received a score.")
    warnings.extend(
        f"{row['market']} coverage is low: {row['analyzed']:,}/{row['expected']:,}."
        for row in markets
        if row["expected"] and row["analyzed"] / row["expected"] < 0.5
    )

    return {
        "summary": {
            "requested": requested,
            "processed": processed,
            "analyzed": len(companies),
            "scored": scored,
            "failed": len(errors),
            "success_rate": len(companies) / requested if requested else None,
            "scored_rate": scored / requested if requested else None,
        },
        "markets": markets,
        "error_categories": dict(sorted(error_categories.items())),
        "rating_counts": dict(sorted(Counter(str(row.get("rating") or "Unknown") for row in companies).items())),
        "comparison": _compare_rankings(current, previous or {}),
        "warnings": warnings,
    }


def _compare_rankings(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    current_rows = {row.get("ticker"): row for row in current.get("companies", []) if row.get("ticker")}
    previous_rows = {row.get("ticker"): row for row in previous.get("companies", []) if row.get("ticker")}
    common = sorted(set(current_rows) & set(previous_rows))
    score_changes = []
    rating_changes = []
    rank_moves = []
    for ticker in common:
        current_row = current_rows[ticker]
        previous_row = previous_rows[ticker]
        current_score = clean_number(current_row.get("overall_score"))
        previous_score = clean_number(previous_row.get("overall_score"))
        if current_score is not None and previous_score is not None:
            score_changes.append(abs(current_score - previous_score))
        if current_row.get("rating") != previous_row.get("rating"):
            rating_changes.append(
                {
                    "ticker": ticker,
                    "from": previous_row.get("rating"),
                    "to": current_row.get("rating"),
                }
            )
        current_rank = clean_number(current_row.get("rank"))
        previous_rank = clean_number(previous_row.get("rank"))
        if current_rank is not None and previous_rank is not None and current_rank != previous_rank:
            rank_moves.append(
                {
                    "ticker": ticker,
                    "from": int(previous_rank),
                    "to": int(current_rank),
                    "movement": int(previous_rank - current_rank),
                }
            )
    rank_moves.sort(key=lambda item: abs(item["movement"]), reverse=True)
    return {
        "previous_available": bool(previous_rows),
        "common": len(common),
        "added": len(set(current_rows) - set(previous_rows)),
        "removed": len(set(previous_rows) - set(current_rows)),
        "mean_absolute_score_change": sum(score_changes) / len(score_changes) if score_changes else None,
        "large_score_changes": sum(change >= 10 for change in score_changes),
        "rating_change_count": len(rating_changes),
        "rating_changes": rating_changes[:100],
        "largest_rank_moves": rank_moves[:20],
    }


def _error_category(message: str) -> str:
    normalized = message.casefold()
    if "429" in normalized or "too many requests" in normalized or "rate limit" in normalized:
        return "rate_limited"
    if "timeout" in normalized or "timed out" in normalized:
        return "timeout"
    if "connection" in normalized or "network" in normalized or "dns" in normalized:
        return "network"
    if "insufficient" in normalized or "missing" in normalized or "no data" in normalized:
        return "missing_data"
    return "provider_or_analysis"
