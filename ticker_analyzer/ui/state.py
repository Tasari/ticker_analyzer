from __future__ import annotations

import time
from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from ticker_analyzer.ticker_symbols import normalize_ticker


def initialize_state() -> None:
    # Streamlit removes widget-owned keys when navigating away from a view.
    # Detach simulation preferences so returning to the page retains its setup.
    for key in list(st.session_state):
        if key.startswith("simulation_"):
            st.session_state[key] = st.session_state[key]
    defaults = {
        "selected_tickers": ["AFRM"],
        "analysis_results": {},
        "analysis_errors": {},
        "analysis_result_version": None,
        "active_ticker": "AFRM",
        "etf_ticker": "VOO",
        "selected_etfs": [st.session_state.get("etf_ticker", "VOO")],
        "growth_range": "2Y",
        "fundamentals_range": "2Y",
        "value_range": "2Y",
        "analysis_pending_changes": False,
        "automatic_analysis_attempted": False,
        "automatic_analysis_requested": False,
        "analysis_pending_since": None,
        "watchlist": [],
        "watchlist_snapshots": {},
        "watchlist_alerts": [],
        "watchlist_growth_range": "2Y",
        "watchlist_fundamentals_range": "2Y",
        "watchlist_value_range": "2Y",
        "watchlist_auto_refresh_attempted": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def remove_tickers_from_state(state: MutableMapping[str, Any], tickers: list[str]) -> None:
    removed = {ticker for ticker in tickers if ticker}
    if not removed:
        return

    state["selected_tickers"] = [
        ticker for ticker in state.get("selected_tickers", []) if ticker not in removed
    ]
    results = state.get("analysis_results", {})
    errors = state.get("analysis_errors", {})
    for ticker in removed:
        results.pop(ticker, None)
        errors.pop(ticker, None)
        state.pop(f"select_remove_{ticker}", None)

    if state.get("active_ticker") in removed:
        state["active_ticker"] = next(iter(results), None)


def add_ticker_to_state(state: MutableMapping[str, Any], value: Any) -> bool:
    return bool(add_tickers_to_state(state, [value]))


def add_companies_to_analyzer(tickers: list[str]) -> None:
    add_tickers_to_state(st.session_state, tickers)
    if any(normalize_ticker(ticker) in st.session_state.get("selected_tickers", []) for ticker in tickers):
        st.session_state["page"] = "Stock Analyzer"


def add_etfs_to_state(state: MutableMapping[str, Any], values: list[Any]) -> list[str]:
    selected = list(state.get("selected_etfs", []))
    added = []
    for value in values:
        ticker = normalize_ticker(value)
        if ticker and ticker != "ACC_STMT" and ticker not in selected:
            selected.append(ticker)
            added.append(ticker)
    state["selected_etfs"] = selected
    state["etf_selection"] = selected.copy()
    if added:
        state["etf_load_requested"] = True
    return added


def add_etfs_to_view(tickers: list[str]) -> None:
    add_etfs_to_state(st.session_state, tickers)
    if st.session_state.get("selected_etfs"):
        st.session_state["etf_load_requested"] = True
        st.session_state["page"] = "ETF"


def add_tickers_to_state(state: MutableMapping[str, Any], values: list[Any]) -> list[str]:
    selected = state.setdefault("selected_tickers", [])
    added: list[str] = []
    for value in values:
        ticker = normalize_ticker(value)
        if not ticker or ticker in selected:
            continue
        selected.append(ticker)
        added.append(ticker)
    if not added:
        return []
    state["analysis_pending_changes"] = True
    state["automatic_analysis_requested"] = False
    state["analysis_pending_since"] = time.time()
    if state.get("active_ticker") not in selected:
        state["active_ticker"] = added[0]
    return added
