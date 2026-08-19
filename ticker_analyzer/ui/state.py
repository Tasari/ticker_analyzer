from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

import streamlit as st


def initialize_state() -> None:
    if "selected_tickers" not in st.session_state:
        st.session_state.selected_tickers = ["AFRM"]
    if "analysis_results" not in st.session_state:
        st.session_state.analysis_results = {}
    if "analysis_errors" not in st.session_state:
        st.session_state.analysis_errors = {}
    if "active_ticker" not in st.session_state:
        st.session_state.active_ticker = "AFRM"


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
