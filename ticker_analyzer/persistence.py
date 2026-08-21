from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime, timedelta
from typing import Any

import streamlit as st

PERSISTENCE_VERSION = 1
PERSISTENCE_TTL = timedelta(days=30)
STORAGE_KEY = "ticker_analyzer.preferences.v1"
VALID_PAGES = {"Stock Analyzer", "Large Cap Ranking"}
VALID_RANGES = {"1Y", "2Y", "3Y"}
RANGE_STATE_KEYS = {
    "Growth": "growth_range",
    "Fundamentals": "fundamentals_range",
    "Value": "value_range",
}
TICKER_PATTERN = re.compile(r"^[A-Z0-9.^=-]{1,32}$")
DISABLE_ENV = "TICKER_ANALYZER_DISABLE_BROWSER_STORAGE"

_BROWSER_STORAGE = st.components.v2.component(
    "ticker_analyzer_browser_storage",
    html='<span aria-hidden="true"></span>',
    # Keep the host mounted so browsers execute the storage bridge. A display:none
    # host can be skipped by Streamlit's frontend and leave hydration pending.
    css=(
        ":host { display: block; width: 1px; height: 1px; overflow: hidden; "
        "opacity: 0; pointer-events: none; }"
    ),
    js="""
        export default function({ parentElement, data, setStateValue }) {
            try {
                if (data.operation === "load") {
                    const raw = window.localStorage.getItem(data.storageKey);
                    setStateValue("response", {loaded: true, raw: raw});
                } else if (data.operation === "save") {
                    window.localStorage.setItem(data.storageKey, data.payload);
                }
            } catch (_error) {
                if (data.operation === "load") {
                    setStateValue("response", {loaded: true, raw: null});
                }
            }
        }
    """,
)


def browser_storage_disabled() -> bool:
    return os.getenv(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def hydrate_browser_state(state: MutableMapping[str, Any]) -> bool:
    """Load browser preferences once, returning False until the frontend responds."""
    if state.get("_browser_preferences_hydrated"):
        return True
    if browser_storage_disabled():
        state["_browser_preferences_hydrated"] = True
        return True

    result = _BROWSER_STORAGE(
        data={"operation": "load", "storageKey": STORAGE_KEY},
        default={"response": {"loaded": False, "raw": None}},
        on_response_change=lambda: None,
        key="browser_preferences_loader",
        width=1,
        height=1,
    )
    response = result.response or {}
    if not response.get("loaded"):
        return False
    snapshot = parse_snapshot(response.get("raw"))
    if snapshot is not None:
        apply_snapshot(state, snapshot)
    state["_browser_preferences_hydrated"] = True
    return True


def persist_browser_state(state: Mapping[str, Any]) -> None:
    """Write compact preferences to this browser without storing analysis results."""
    if browser_storage_disabled() or not state.get("_browser_preferences_hydrated"):
        return
    payload = json.dumps(build_snapshot(state), separators=(",", ":"), sort_keys=True)
    _BROWSER_STORAGE(
        data={"operation": "save", "storageKey": STORAGE_KEY, "payload": payload},
        key="browser_preferences_writer",
        width=1,
        height=1,
    )


def build_snapshot(state: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    tickers = normalize_tickers(state.get("selected_tickers"))
    active_ticker = normalize_ticker(state.get("active_ticker"))
    if active_ticker not in tickers:
        active_ticker = tickers[0] if tickers else None
    ranges = {
        tab: normalize_range(state.get(state_key))
        for tab, state_key in RANGE_STATE_KEYS.items()
    }
    page = state.get("page")
    return {
        "version": PERSISTENCE_VERSION,
        "saved_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "selected_tickers": tickers,
        "active_ticker": active_ticker,
        "ranges": ranges,
        "page": page if page in VALID_PAGES else "Stock Analyzer",
    }


def parse_snapshot(raw: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
        saved_at = datetime.fromisoformat(str(payload.get("saved_at", "")).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != PERSISTENCE_VERSION:
        return None
    if saved_at.tzinfo is None:
        saved_at = saved_at.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if saved_at > current + timedelta(minutes=5) or current - saved_at > PERSISTENCE_TTL:
        return None
    return build_snapshot(payload_to_state(payload), now=saved_at)


def apply_snapshot(state: MutableMapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    tickers = normalize_tickers(snapshot.get("selected_tickers"))
    state["selected_tickers"] = tickers
    active_ticker = normalize_ticker(snapshot.get("active_ticker"))
    state["active_ticker"] = active_ticker if active_ticker in tickers else (tickers[0] if tickers else None)
    ranges = snapshot.get("ranges") if isinstance(snapshot.get("ranges"), Mapping) else {}
    for tab, state_key in RANGE_STATE_KEYS.items():
        state[state_key] = normalize_range(ranges.get(tab))
    page = snapshot.get("page")
    state["page"] = page if page in VALID_PAGES else "Stock Analyzer"
    # Results deliberately stay session-only so a returning user gets fresh data.
    state["analysis_results"] = {}
    state["analysis_errors"] = {}


def payload_to_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    ranges = payload.get("ranges") if isinstance(payload.get("ranges"), Mapping) else {}
    return {
        "selected_tickers": payload.get("selected_tickers"),
        "active_ticker": payload.get("active_ticker"),
        "page": payload.get("page"),
        **{state_key: ranges.get(tab) for tab, state_key in RANGE_STATE_KEYS.items()},
    }


def normalize_tickers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value[:50]:
        ticker = normalize_ticker(item)
        if ticker and ticker not in normalized:
            normalized.append(ticker)
    return normalized


def normalize_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper().replace("/", "-")
    return ticker if TICKER_PATTERN.fullmatch(ticker) else None


def normalize_range(value: Any) -> str:
    return str(value).upper() if str(value).upper() in VALID_RANGES else "2Y"
