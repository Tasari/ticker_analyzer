from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ticker_analyzer.numbers import clean_number
from ticker_analyzer.ticker_symbols import normalize_ticker

WATCHLIST_LIMIT = 50
ALERT_LIMIT = 100
THRESHOLD_FIELDS = ("price_above", "price_below", "score_above", "score_below")


@dataclass(frozen=True)
class WatchlistRefresh:
    snapshots: dict[str, dict[str, Any]]
    alerts: list[dict[str, Any]]
    retry: dict[str, str]


def normalize_watchlist(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value[:WATCHLIST_LIMIT]:
        item = raw if isinstance(raw, dict) else {"ticker": raw}
        ticker = normalize_ticker(item.get("ticker"))
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(
            {
                "ticker": ticker,
                **{field: _optional_non_negative(item.get(field)) for field in THRESHOLD_FIELDS},
            }
        )
    return normalized


def normalize_snapshots(value: Any, watchlist: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    watched = {item["ticker"] for item in watchlist}
    snapshots: dict[str, dict[str, Any]] = {}
    for raw_ticker, raw in value.items():
        ticker = normalize_ticker(raw_ticker)
        if ticker not in watched or not isinstance(raw, dict):
            continue
        snapshots[ticker] = {
            "ticker": ticker,
            "company_name": _short_text(raw.get("company_name")),
            "price": _optional_number(raw.get("price")),
            "score": _optional_number(raw.get("score")),
            "rating": _short_text(raw.get("rating")),
            "missing": _normalize_missing(raw.get("missing")),
            "conditions": _normalize_conditions(raw.get("conditions")),
            "status": "error" if raw.get("status") == "error" else "ok",
            "error": _short_text(raw.get("error")),
            "checked_at": _short_text(raw.get("checked_at")),
        }
    return snapshots


def normalize_alerts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    alerts: list[dict[str, Any]] = []
    for raw in value[:ALERT_LIMIT]:
        if not isinstance(raw, dict):
            continue
        ticker = normalize_ticker(raw.get("ticker"))
        message = _short_text(raw.get("message"), maximum=500)
        kind = _short_text(raw.get("kind"))
        created_at = _short_text(raw.get("created_at"))
        if not ticker or not message or not kind or not created_at:
            continue
        alerts.append(
            {
                "ticker": ticker,
                "kind": kind,
                "message": message,
                "created_at": created_at,
                "read": bool(raw.get("read")),
            }
        )
    return alerts


def add_watch_ticker(items: Any, value: Any) -> tuple[list[dict[str, Any]], bool]:
    normalized = normalize_watchlist(items)
    ticker = normalize_ticker(value)
    if (
        not ticker
        or ticker == "ACC_STMT"
        or ticker in {item["ticker"] for item in normalized}
        or len(normalized) >= WATCHLIST_LIMIT
    ):
        return normalized, False
    normalized.append({"ticker": ticker, **{field: None for field in THRESHOLD_FIELDS}})
    return normalized, True


def evaluate_watchlist(
    items: Any,
    previous_snapshots: Any,
    results: dict[str, dict[str, Any]],
    errors: dict[str, str],
    *,
    checked_at: datetime | None = None,
) -> WatchlistRefresh:
    watchlist = normalize_watchlist(items)
    previous = normalize_snapshots(previous_snapshots, watchlist)
    timestamp = (checked_at or datetime.now(UTC)).astimezone(UTC).isoformat()
    snapshots: dict[str, dict[str, Any]] = {}
    alerts: list[dict[str, Any]] = []
    retry: dict[str, str] = {}

    for item in watchlist:
        ticker = item["ticker"]
        old = previous.get(ticker)
        error = errors.get(ticker)
        result = results.get(ticker)
        if error or result is None:
            message = str(error or "Analysis did not return a result.")
            retry[ticker] = message
            snapshots[ticker] = {
                **(old or _empty_snapshot(ticker)),
                "status": "error",
                "error": message,
                "checked_at": timestamp,
            }
            if old is None or old.get("status") != "error" or old.get("error") != message:
                alerts.append(_alert(ticker, "retry", f"Check again: {message}", timestamp))
            continue

        current = _snapshot_from_result(ticker, result, item, timestamp)
        snapshots[ticker] = current
        alerts.extend(_change_alerts(item, old, current, timestamp))

    return WatchlistRefresh(snapshots=snapshots, alerts=alerts, retry=retry)


def _snapshot_from_result(
    ticker: str,
    result: dict[str, Any],
    item: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    price = _optional_number(result.get("current_price"))
    score = _optional_number(result.get("overall_score"))
    rating = _short_text(result.get("rating"))
    missing = set(_normalize_missing(result.get("missing")))
    if price is None:
        missing.add("Current price")
    if score is None:
        missing.add("Overall score")
    if not rating or rating == "Not Rated":
        missing.add("Overall rating")
    return {
        "ticker": ticker,
        "company_name": _short_text(result.get("company_name")) or ticker,
        "price": price,
        "score": score,
        "rating": rating,
        "missing": sorted(missing),
        "conditions": _threshold_conditions(item, price, score),
        "status": "ok",
        "error": None,
        "checked_at": timestamp,
    }


def _change_alerts(
    item: dict[str, Any],
    old: dict[str, Any] | None,
    current: dict[str, Any],
    timestamp: str,
) -> list[dict[str, Any]]:
    ticker = item["ticker"]
    alerts: list[dict[str, Any]] = []
    if old and old.get("status") == "error":
        alerts.append(_alert(ticker, "recovered", "Data refresh recovered successfully.", timestamp))
    old_rating = old.get("rating") if old else None
    new_rating = current.get("rating")
    if old_rating and new_rating and old_rating != new_rating:
        alerts.append(_alert(ticker, "rating", f"Rating changed: {old_rating} -> {new_rating}.", timestamp))

    old_conditions = old.get("conditions", {}) if old else {}
    for key, reached in current.get("conditions", {}).items():
        if reached and not old_conditions.get(key, False):
            alerts.append(_alert(ticker, "threshold", _threshold_message(key), timestamp))

    old_missing = set(old.get("missing", [])) if old else set()
    current_missing = set(current.get("missing", []))
    newly_missing = sorted(current_missing - old_missing)
    newly_available = sorted(old_missing - current_missing)
    if newly_missing:
        alerts.append(
            _alert(ticker, "missing_data", f"New missing data: {_summarize(newly_missing)}.", timestamp)
        )
    if newly_available:
        alerts.append(
            _alert(ticker, "new_data", f"Data now available: {_summarize(newly_available)}.", timestamp)
        )
    return alerts


def _threshold_conditions(
    item: dict[str, Any],
    price: float | None,
    score: float | None,
) -> dict[str, bool]:
    conditions: dict[str, bool] = {}
    values = {
        "price_above": price,
        "price_below": price,
        "score_above": score,
        "score_below": score,
    }
    for field in THRESHOLD_FIELDS:
        threshold = item.get(field)
        current = values[field]
        if threshold is None:
            continue
        key = f"{field}:{threshold:g}"
        conditions[key] = bool(
            current is not None
            and (current >= threshold if field.endswith("above") else current <= threshold)
        )
    return conditions


def _threshold_message(key: str) -> str:
    field, raw_threshold = key.split(":", maxsplit=1)
    subject = "Price" if field.startswith("price") else "Score"
    direction = "reached or exceeded" if field.endswith("above") else "reached or fell below"
    return f"{subject} {direction} {raw_threshold}."


def _empty_snapshot(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "company_name": ticker,
        "price": None,
        "score": None,
        "rating": None,
        "missing": [],
        "conditions": {},
        "status": "error",
        "error": None,
        "checked_at": None,
    }


def _alert(ticker: str, kind: str, message: str, created_at: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "kind": kind,
        "message": message,
        "created_at": created_at,
        "read": False,
    }


def _normalize_missing(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({text for item in list(value)[:100] if (text := _short_text(item, maximum=300))})


def _normalize_conditions(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {str(key)[:100]: bool(reached) for key, reached in list(value.items())[:20]}


def _optional_non_negative(value: Any) -> float | None:
    number = _optional_number(value)
    return number if number is not None and number >= 0 else None


def _optional_number(value: Any) -> float | None:
    number = clean_number(value)
    return float(number) if number is not None else None


def _short_text(value: Any, *, maximum: int = 200) -> str | None:
    text = str(value or "").strip()
    return text[:maximum] if text else None


def _summarize(values: list[str]) -> str:
    preview = values[:3]
    suffix = f" (+{len(values) - len(preview)} more)" if len(values) > len(preview) else ""
    return "; ".join(preview) + suffix
