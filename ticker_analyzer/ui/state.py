from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from ticker_analyzer.ticker_symbols import normalize_ticker


def initialize_state() -> None:
    if "selected_tickers" not in st.session_state:
        st.session_state["selected_tickers"] = ["AFRM"]
    if "analysis_results" not in st.session_state:
        st.session_state["analysis_results"] = {}
    if "analysis_errors" not in st.session_state:
        st.session_state["analysis_errors"] = {}
    if "active_ticker" not in st.session_state:
        st.session_state["active_ticker"] = "AFRM"
    if "growth_range" not in st.session_state:
        st.session_state["growth_range"] = "2Y"
    if "fundamentals_range" not in st.session_state:
        st.session_state["fundamentals_range"] = "2Y"
    if "value_range" not in st.session_state:
        st.session_state["value_range"] = "2Y"
    if "analysis_pending_changes" not in st.session_state:
        st.session_state["analysis_pending_changes"] = False
    if "automatic_analysis_attempted" not in st.session_state:
        st.session_state["automatic_analysis_attempted"] = False


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
    if state.get("active_ticker") not in selected:
        state["active_ticker"] = added[0]
    return added
