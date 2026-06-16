from __future__ import annotations

import streamlit as st
from ticker_analyzer import load_config
from ticker_analyzer.ui.actions import analyze_selected_tickers
from ticker_analyzer.ui.state import initialize_state
from ticker_analyzer.ui.views import (
    render_analysis_errors,
    render_company_analysis,
    render_multi_ticker_analysis,
    render_sidebar,
)

st.set_page_config(page_title="Stock Analyzer", page_icon="chart_with_upwards_trend", layout="wide")


def main() -> None:
    st.title("Stock Analyzer")
    st.caption("Rule-based stock analysis using available yfinance data. This is not financial advice.")

    config = load_config()
    initialize_state()
    ranges, analyze_clicked = render_sidebar(config)

    if analyze_clicked or not st.session_state.analysis_results:
        with st.spinner("Fetching market and financial data..."):
            st.session_state.analysis_results, st.session_state.analysis_errors = analyze_selected_tickers(
                st.session_state.selected_tickers,
                ranges,
                config,
            )
            available_tickers = list(st.session_state.analysis_results)
            if available_tickers and st.session_state.active_ticker not in available_tickers:
                st.session_state.active_ticker = available_tickers[0]

    render_analysis_errors(st.session_state.analysis_errors)
    results = st.session_state.analysis_results
    if not results:
        if st.session_state.selected_tickers:
            st.error("No selected ticker could be analyzed.")
        else:
            st.info("Add a ticker to start the analysis.")
        return

    if len(results) == 1:
        render_company_analysis(next(iter(results.values())))
        return

    render_multi_ticker_analysis(results)


if __name__ == "__main__":
    main()
