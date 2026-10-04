from __future__ import annotations

import streamlit as st
from ticker_analyzer.access_control import (
    render_access_gate,
    render_logout_control,
    render_runtime_disclaimer,
)

st.set_page_config(page_title="Stock Analyzer", page_icon="chart_with_upwards_trend", layout="wide")


def main() -> None:
    if not render_access_gate():
        return

    from ticker_analyzer.navigation import PAGE_OPTIONS, VALID_PAGES
    from ticker_analyzer.persistence import hydrate_browser_state, persist_browser_state
    from ticker_analyzer.ui import views
    from ticker_analyzer.ui.state import initialize_state

    st.title("Stock Analyzer")
    st.caption(
        "Rule-based stock analysis with source provenance and point-in-time safeguards. This is not financial advice."
    )

    browser_state_ready = hydrate_browser_state(st.session_state)
    if not browser_state_ready:
        st.caption("Restoring your saved companies and preferences in the background...")
    initialize_state()
    render_logout_control()
    render_runtime_disclaimer()

    try:
        if st.session_state.get("page") not in VALID_PAGES:
            st.session_state.page = "Stock Analyzer"
        page = st.sidebar.radio(
            "View",
            PAGE_OPTIONS,
            key="page",
        )
        st.sidebar.caption("This browser remembers your companies and preferences for 30 days.")
        if page in {"ETF", "Simulation"}:
            from ticker_analyzer.ui.sidebar import render_company_selector

            render_company_selector()
        if page == "ETF":
            from ticker_analyzer.ui.etf_view import render_etf

            render_etf()
            return
        if page == "Simulation":
            from ticker_analyzer.ui.simulation_view import render_simulation

            st.sidebar.caption(
                "Simulation uses companies analyzed in Stock Analyzer and imported Account Statement returns."
            )
            render_simulation(st.session_state.analysis_results)
            return
        if page == "Large Cap Ranking":
            views.render_large_cap_ranking()
            return
        if page == "Account Statement":
            views.render_account_statement()
            return

        from ticker_analyzer.ui.stock_view import render_stock_analyzer

        render_stock_analyzer(browser_state_ready)
    finally:
        persist_browser_state(st.session_state)


if __name__ == "__main__":
    main()
