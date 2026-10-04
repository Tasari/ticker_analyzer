from __future__ import annotations

import plotly.express as px
import streamlit as st
from streamlit_searchbox import st_searchbox

from ticker_analyzer.providers.etf import EtfDataError, EtfHoldings, fetch_etf_holdings
from ticker_analyzer.ticker_symbols import normalize_ticker
from ticker_analyzer.ui.etf_actions import search_etfs, selected_holding_tickers
from ticker_analyzer.ui.etf_joint_view import render_joint
from ticker_analyzer.ui.state import add_companies_to_analyzer, add_etfs_to_state


@st.cache_data(ttl=3600, max_entries=128, show_spinner=False)
def cached_etf_holdings(ticker: str) -> EtfHoldings:
    return fetch_etf_holdings(ticker)


def render_etf() -> None:
    st.subheader("ETF Holdings")
    st.caption("Explore the largest positions and their percentage of the whole fund. This is a partial holdings list.")
    selected = st_searchbox(
        search_etfs, key="etf_search", label="Add ETF",
        placeholder="Type ETF ticker or fund name", edit_after_submit="disabled",
        clear_on_submit=True, debounce=250,
        help="Search ETFs by name or ticker, or select funds in Large Cap Ranking → ETFs.",
    )
    if selected:
        if add_etfs_to_state(st.session_state, [selected.split(" | ", maxsplit=1)[0]]):
            st.rerun()
    with st.expander("Add exact ETF tickers", expanded=False):
        with st.form("etf_lookup"):
            symbols = st.text_input("ETF tickers", placeholder="VVSM.DE, VOO, EUNL.DE")
            submitted = st.form_submit_button("Add ETFs")
        if submitted:
            values = symbols.replace(",", " ").replace(";", " ").split()
            if not values or any(not normalize_ticker(value) or normalize_ticker(value) == "ACC_STMT" for value in values):
                st.error("Enter valid ETF tickers separated by commas or spaces.")
            else:
                add_etfs_to_state(st.session_state, values)
                st.session_state["etf_load_requested"] = True
                st.rerun()
    st.caption("Use the full Yahoo ticker for international funds, including the exchange suffix (for example VVSM.DE).")
    tickers = list(st.session_state.get("selected_etfs", []))
    st.session_state["etf_selection"] = tickers.copy()
    st.multiselect(
        "Selected ETFs", tickers, key="etf_selection",
        on_change=_update_etf_selection,
        help="Keep several funds selected. Remove a chip to remove that ETF from this view.",
    )
    show = st.button("Show holdings", type="primary", disabled=not tickers)
    results = st.session_state.setdefault("etf_holdings_by_ticker", {})
    errors = st.session_state.setdefault("etf_errors", {})
    # Migrate the previous single-fund session without losing a downloaded table.
    legacy = st.session_state.pop("etf_holdings", None)
    if isinstance(legacy, EtfHoldings):
        results.setdefault(legacy.ticker, legacy)
    requested = st.session_state.pop("etf_load_requested", False)
    if show or requested:
        with st.spinner("Fetching ETF holdings..."):
            for ticker in tickers:
                results.pop(ticker, None)
                errors.pop(ticker, None)
                try:
                    results[ticker] = cached_etf_holdings(ticker)
                except EtfDataError as exc:
                    errors[ticker] = str(exc)
                except Exception:
                    errors[ticker] = f"ETF holdings could not be downloaded for {ticker}. Try again later."
    if not tickers:
        st.info("Search for ETFs or select them in Large Cap Ranking → ETFs.")
        return
    holdings_tab, joint_tab = st.tabs(["Holdings", "Joint"])
    with holdings_tab:
        count = st.selectbox("Largest positions per ETF", [5, 10, 25], index=1, key="etf_position_count")
        for ticker in tickers:
            if ticker in errors:
                st.warning(errors[ticker])
            elif isinstance(results.get(ticker), EtfHoldings):
                _render_holdings(results[ticker], count)
            else:
                st.info(f"Select Show holdings to load {ticker}.")
    with joint_tab:
        render_joint(tickers, results)


def _update_etf_selection() -> None:
    st.session_state["selected_etfs"] = list(st.session_state["etf_selection"])


def _render_holdings(result: EtfHoldings, count: int) -> None:
    st.markdown(f"### {result.ticker} — largest holdings")
    st.caption(
        f"[Source: {result.source}]({result.source_url}) · Holdings date: {result.as_of or 'not supplied'} · "
        f"Retrieved: {result.fetched_at:%Y-%m-%d %H:%M} UTC"
    )
    for warning in result.warnings:
        st.caption(warning)
    displayed = result.holdings.head(count)
    columns = st.columns(2)
    columns[0].metric("Positions shown", len(displayed))
    columns[1].metric("Share of the fund shown", f"{displayed['Weight (%)'].sum():.2f}%")
    table_event = st.dataframe(
        displayed, hide_index=True, width="stretch",
        key=f"etf_holdings_table_{result.ticker}_{count}_{result.fetched_at.isoformat()}",
        on_select="rerun", selection_mode="multi-row",
        column_config={"Weight (%)": st.column_config.NumberColumn("Weight (%)", format="%.2f%%")},
    )
    selected_tickers = selected_holding_tickers(displayed, table_event.selection.rows, result.source)
    st.button(
        "Add selected companies to Analyzer", type="primary",
        key=f"etf_add_companies_{result.ticker}",
        disabled=not selected_tickers,
        help="Select one or more rows, then add those companies to Stock Analyzer.",
        on_click=add_companies_to_analyzer, args=(selected_tickers,),
    )
    chart_data = displayed.assign(Position=displayed["Company"] + " (" + displayed["Ticker"] + ")")
    figure = px.bar(chart_data, x="Weight (%)", y="Position", orientation="h", text="Weight (%)")
    figure.update_traces(texttemplate="%{x:.2f}%", textposition="outside")
    figure.update_layout(yaxis={"autorange": "reversed"}, height=max(300, 32 * len(displayed)), margin={"t": 10})
    st.plotly_chart(figure, width="stretch", key=f"etf_chart_{result.ticker}")
    st.caption("Weights refer to the entire fund. Other holdings and assets account for the remaining allocation. Holdings can change after the reported date.")
