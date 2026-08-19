from __future__ import annotations

import streamlit as st
from streamlit_searchbox import st_searchbox

from ticker_analyzer.ui.analysis_actions import search_tickers
from ticker_analyzer.ui.config_view import render_config_editor
from ticker_analyzer.ui.state import remove_tickers_from_state


def render_sidebar(config: dict) -> tuple[dict[str, str], bool]:
    with st.sidebar:
        st.header("Analysis")
        render_ticker_search()
        render_selected_tickers()
        range_options = ["1Y", "2Y", "3Y"]
        growth_range = st.selectbox("Growth range", range_options, index=1)
        fundamentals_range = st.selectbox("Fundamentals range", range_options, index=1)
        value_range = st.selectbox("Value range", range_options, index=1)
        analyze_clicked = st.button(
            "Analyze",
            type="primary",
            width="stretch",
            disabled=not st.session_state.selected_tickers,
        )
        st.divider()
        render_config_editor(config)
    return {
        "Growth": growth_range,
        "Fundamentals": fundamentals_range,
        "Value": value_range,
    }, analyze_clicked


def render_ticker_search() -> None:
    selected = st_searchbox(
        search_tickers,
        key="ticker_search",
        label="Add ticker",
        placeholder="Type ticker or company name",
        edit_after_submit="disabled",
        clear_on_submit=True,
        debounce=250,
        help="Search by ticker or company name, then add it to the comparison queue.",
    )
    if not selected:
        return
    ticker = selected.split(" | ", maxsplit=1)[0].strip().upper()
    if ticker in st.session_state.selected_tickers:
        return
    st.session_state.selected_tickers.append(ticker)
    st.session_state.analysis_results = {}
    st.rerun()


def render_selected_tickers() -> None:
    st.caption("Selected stocks")
    if not st.session_state.selected_tickers:
        st.write("No stocks selected.")
        return

    tickers = list(st.session_state.selected_tickers)
    for ticker in tickers:
        select_col, label_col, remove_col = st.columns([1, 4, 1])
        select_col.checkbox(
            f"Select {ticker}",
            key=f"select_remove_{ticker}",
            label_visibility="collapsed",
            help=f"Select {ticker} for removal",
        )
        label_col.write(ticker)
        remove_col.button(
            "X",
            key=f"remove_{ticker}",
            help=f"Remove {ticker}",
            on_click=remove_selected_tickers,
            args=([ticker],),
        )

    selected = [ticker for ticker in tickers if st.session_state.get(f"select_remove_{ticker}")]
    st.button(
        f"Remove selected ({len(selected)})",
        width="stretch",
        disabled=not selected,
        on_click=remove_selected_tickers,
        args=(selected,),
    )


def remove_selected_tickers(tickers: list[str]) -> None:
    remove_tickers_from_state(st.session_state, tickers)
