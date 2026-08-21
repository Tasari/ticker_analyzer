from __future__ import annotations

import streamlit as st
from ticker_analyzer.persistence import hydrate_browser_state, persist_browser_state
from ticker_analyzer.ui import views
from ticker_analyzer.ui.state import initialize_state

st.set_page_config(page_title="Stock Analyzer", page_icon="chart_with_upwards_trend", layout="wide")


def main() -> None:
    st.title("Stock Analyzer")
    st.caption("Rule-based stock analysis with source provenance and point-in-time safeguards. This is not financial advice.")

    browser_state_ready = hydrate_browser_state(st.session_state)
    if not browser_state_ready:
        st.caption("Restoring your saved companies and preferences in the background...")
    initialize_state()

    try:
        page = st.sidebar.radio("View", ["Stock Analyzer", "Large Cap Ranking"], key="page")
        st.sidebar.caption("This browser remembers your companies and ranges for 30 days.")
        if page == "Large Cap Ranking":
            views.render_large_cap_ranking()
            return

        from ticker_analyzer.config import load_config
        from ticker_analyzer.ui.analysis_actions import analyze_selected_tickers

        config = load_config()
        ranges, analyze_clicked = views.render_sidebar(config)

        if analyze_clicked or (browser_state_ready and not st.session_state.analysis_results):
            with st.spinner("Fetching market and financial data..."):
                st.session_state.analysis_results, st.session_state.analysis_errors = analyze_selected_tickers(
                    st.session_state.selected_tickers,
                    ranges,
                    config,
                )
                available_tickers = list(st.session_state.analysis_results)
                if available_tickers and st.session_state.active_ticker not in available_tickers:
                    st.session_state.active_ticker = available_tickers[0]

        views.render_analysis_errors(st.session_state.analysis_errors)
        results = st.session_state.analysis_results
        if not results:
            if not browser_state_ready and st.session_state.selected_tickers:
                st.info("Saved preferences are still loading. You can continue or click Analyze now.")
            elif st.session_state.selected_tickers:
                st.error("No selected ticker could be analyzed.")
            else:
                st.info("Add a ticker to start the analysis.")
            return

        if len(results) == 1:
            views.render_company_analysis(next(iter(results.values())))
            return

        views.render_multi_ticker_analysis(results)
    finally:
        persist_browser_state(st.session_state)


if __name__ == "__main__":
    main()
