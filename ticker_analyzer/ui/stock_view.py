"""Stock analysis lifecycle and rendering, separate from application routing."""

from __future__ import annotations

import time

import streamlit as st

from ticker_analyzer.ui import views


def render_stock_analyzer(browser_state_ready: bool) -> None:
    from ticker_analyzer.config import load_config
    from ticker_analyzer.portfolio.returns import (
        ACCOUNT_RETURNS_STATE_KEY,
        ACCOUNT_STATEMENT_TICKER,
        ReturnsTable,
    )
    from ticker_analyzer.ui.analysis_actions import ANALYSIS_RESULT_VERSION, analyze_selected_tickers

    config = load_config()
    ranges, analyze_clicked = views.render_sidebar(config)
    market_tickers = [ticker for ticker in st.session_state.selected_tickers if ticker != ACCOUNT_STATEMENT_TICKER]
    restored_setup_analysis = (
        browser_state_ready
        and not st.session_state.analysis_results
        and not st.session_state.analysis_pending_changes
        and not st.session_state.automatic_analysis_attempted
    )
    stale_runtime_results = bool(st.session_state.analysis_results) and (
        st.session_state.analysis_result_version != ANALYSIS_RESULT_VERSION
    )
    automatic_analysis = (
        st.session_state.automatic_analysis_requested or restored_setup_analysis or stale_runtime_results
    )
    if analyze_clicked or automatic_analysis:
        st.session_state.automatic_analysis_attempted = True
        st.session_state.automatic_analysis_requested = False
        st.session_state.analysis_pending_changes = False
        st.session_state.analysis_pending_since = None
        with st.spinner("Fetching market and financial data..."):
            st.session_state.analysis_results, st.session_state.analysis_errors = analyze_selected_tickers(
                market_tickers,
                ranges,
                config,
                cache_token=time.time_ns() if analyze_clicked else 0,
            )
            st.session_state.analysis_result_version = ANALYSIS_RESULT_VERSION
            available_tickers = list(st.session_state.analysis_results)
            if available_tickers and st.session_state.active_ticker not in available_tickers:
                st.session_state.active_ticker = available_tickers[0]

    views.render_analysis_errors(st.session_state.analysis_errors)
    results = st.session_state.analysis_results
    account_returns_available = (
        isinstance(
            st.session_state.get(ACCOUNT_RETURNS_STATE_KEY),
            ReturnsTable,
        )
        and ACCOUNT_STATEMENT_TICKER in st.session_state.selected_tickers
    )
    if not results and not account_returns_available:
        if not browser_state_ready and st.session_state.selected_tickers:
            st.info("Saved preferences are still loading. You can continue or click Analyze now.")
        elif st.session_state.selected_tickers:
            st.error("No selected ticker could be analyzed.")
        else:
            st.info("Add a ticker to start the analysis.")
        return

    if not results:
        st.info("ACC_STMT is available in the separate Simulation view.")
    elif len(results) == 1:
        views.render_company_analysis(next(iter(results.values())))
    else:
        views.render_multi_ticker_analysis(results)
