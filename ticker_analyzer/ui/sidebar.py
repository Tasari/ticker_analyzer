from __future__ import annotations

import math
import time

import streamlit as st
from streamlit_searchbox import st_searchbox

from ticker_analyzer.portfolio.returns import (
    ACCOUNT_RETURNS_STATE_KEY,
    ACCOUNT_STATEMENT_TICKER,
    ReturnsTable,
)
from ticker_analyzer.ticker_symbols import MARKET_SUFFIXES, ticker_for_market
from ticker_analyzer.ui.analysis_actions import search_tickers
from ticker_analyzer.ui.config_view import render_config_editor
from ticker_analyzer.ui.state import add_ticker_to_state, remove_tickers_from_state

AUTO_ANALYSIS_DELAY_SECONDS = 10


def seconds_until_auto_analysis(started_at: float | None, now: float | None = None) -> int:
    if started_at is None:
        return AUTO_ANALYSIS_DELAY_SECONDS
    current = time.time() if now is None else now
    return max(0, math.ceil(AUTO_ANALYSIS_DELAY_SECONDS - (current - started_at)))


@st.fragment(run_every=1)
def render_auto_analysis_countdown() -> None:
    if not st.session_state.get("analysis_pending_changes"):
        return
    if st.session_state.get("automatic_analysis_requested"):
        return
    remaining = seconds_until_auto_analysis(st.session_state.get("analysis_pending_since"))
    if remaining > 0:
        st.caption(
            f"Selection changed. Add more tickers or wait {remaining}s — analysis will start automatically."
        )
        return
    st.session_state["automatic_analysis_requested"] = True
    st.rerun()


def render_sidebar(config: dict) -> tuple[dict[str, str], bool]:
    with st.sidebar:
        st.header("Analysis")
        render_ticker_search()
        render_account_statement_ticker()
        render_selected_tickers()
        range_options = ["1Y", "2Y", "3Y"]
        growth_range = st.selectbox("Growth range", range_options, key="growth_range")
        fundamentals_range = st.selectbox("Fundamentals range", range_options, key="fundamentals_range")
        value_range = st.selectbox("Value range", range_options, key="value_range")
        analyze_clicked = st.button(
            "Analyze",
            type="primary",
            width="stretch",
            disabled=not st.session_state.selected_tickers,
        )
        if st.session_state.get("analysis_pending_changes"):
            render_auto_analysis_countdown()
        overwrite_preferences = st.button(
            "Save / overwrite remembered setup",
            width="stretch",
            help=(
                "Replace the setup remembered in this browser with the current "
                "companies, active company, page, and analysis ranges."
            ),
        )
        if overwrite_preferences:
            st.success("Remembered setup overwritten with the current settings.")
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
        _render_manual_market_ticker()
        return
    ticker = selected.split(" | ", maxsplit=1)[0]
    if add_ticker_to_state(st.session_state, ticker):
        st.rerun()


def _render_manual_market_ticker() -> None:
    with st.expander("Add exact ticker from another market", expanded=False):
        market = st.selectbox(
            "Market",
            options=list(MARKET_SUFFIXES),
            help="Choose a market to append its Yahoo Finance suffix, or enter a complete Yahoo symbol.",
            key="manual_ticker_market",
        )
        local_symbol = st.text_input(
            "Ticker symbol",
            placeholder="Examples: PKN, LLOY, VOW3, 9988",
            key="manual_ticker_symbol",
        )
        ticker = ticker_for_market(local_symbol, market)
        if ticker:
            st.caption(f"Yahoo ticker: {ticker}")
        if st.button(
            "Add exact ticker",
            disabled=ticker is None,
            width="stretch",
            key="add_manual_ticker",
        ):
            if add_ticker_to_state(st.session_state, ticker):
                st.rerun()
            else:
                st.info("This ticker is already selected.")


def render_account_statement_ticker() -> None:
    returns_table = st.session_state.get(ACCOUNT_RETURNS_STATE_KEY)
    if not isinstance(returns_table, ReturnsTable):
        return
    selected = st.session_state.get("selected_tickers", [])
    if ACCOUNT_STATEMENT_TICKER in selected:
        st.caption(f"{ACCOUNT_STATEMENT_TICKER} uses your imported Account Statement returns.")
        return
    if st.button(
        f"Add {ACCOUNT_STATEMENT_TICKER}",
        width="stretch",
        help="Add the imported Account Statement portfolio to Simulation.",
        key="add_account_statement_ticker",
    ):
        add_ticker_to_state(st.session_state, ACCOUNT_STATEMENT_TICKER)
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
        label = f"{ticker} (portfolio)" if ticker == ACCOUNT_STATEMENT_TICKER else ticker
        label_col.write(label)
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
