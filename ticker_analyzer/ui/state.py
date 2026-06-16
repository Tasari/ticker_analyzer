from __future__ import annotations

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
