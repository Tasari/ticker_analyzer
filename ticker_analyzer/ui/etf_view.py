from __future__ import annotations

import plotly.express as px
import streamlit as st

from ticker_analyzer.providers.etf import EtfDataError, EtfHoldings, fetch_etf_holdings
from ticker_analyzer.ticker_symbols import normalize_ticker


@st.cache_data(ttl=3600, max_entries=32, show_spinner=False)
def cached_etf_holdings(ticker: str) -> EtfHoldings:
    return fetch_etf_holdings(ticker)


def render_etf() -> None:
    st.subheader("ETF Holdings")
    st.caption("Explore the largest positions and their percentage of the whole fund. This is a partial holdings list.")
    with st.form("etf_lookup"):
        symbol = st.text_input("ETF ticker", value=st.session_state.get("etf_ticker", "VOO"), placeholder="VOO, SPY, QQQ, EUNL.DE")
        submitted = st.form_submit_button("Show holdings", type="primary")
    st.caption("Use the full Yahoo ticker for international funds, including the exchange suffix (for example EUNL.DE).")
    if submitted:
        ticker = normalize_ticker(symbol)
        if not ticker:
            st.error("Enter a valid ETF ticker.")
            return
        st.session_state["etf_ticker"] = ticker
        # Clear the old fund before loading, so a failure never shows another
        # ETF's positions underneath the ticker the user just requested.
        st.session_state.pop("etf_holdings", None)
        try:
            with st.spinner("Fetching ETF holdings..."):
                st.session_state["etf_holdings"] = cached_etf_holdings(ticker)
        except EtfDataError as exc:
            st.warning(str(exc))
            return
        except Exception:
            st.warning("ETF holdings could not be downloaded. Try again later.")
            return
    result = st.session_state.get("etf_holdings")
    if not isinstance(result, EtfHoldings):
        st.info("Enter an ETF ticker and select Show holdings.")
        return
    st.markdown(f"### {result.ticker} — largest holdings")
    st.caption(
        f"[Source: {result.source}]({result.source_url}) · Holdings date: {result.as_of or 'not supplied'} · "
        f"Retrieved: {result.fetched_at:%Y-%m-%d %H:%M} UTC"
    )
    for warning in result.warnings:
        st.caption(warning)
    count = st.selectbox("Largest positions", [5, 10, 25], index=1, key="etf_position_count")
    displayed = result.holdings.head(count)
    columns = st.columns(2)
    columns[0].metric("Positions shown", len(displayed))
    columns[1].metric("Share of the fund shown", f"{displayed['Weight (%)'].sum():.2f}%")
    st.dataframe(
        displayed, hide_index=True, width="stretch",
        column_config={"Weight (%)": st.column_config.NumberColumn("Weight (%)", format="%.2f%%")},
    )
    chart_data = displayed.assign(Position=displayed["Company"] + " (" + displayed["Ticker"] + ")")
    figure = px.bar(chart_data, x="Weight (%)", y="Position", orientation="h", text="Weight (%)")
    figure.update_traces(texttemplate="%{x:.2f}%", textposition="outside")
    figure.update_layout(yaxis={"autorange": "reversed"}, height=max(300, 32 * len(displayed)), margin={"t": 10})
    st.plotly_chart(figure, width="stretch")
    st.caption("Weights refer to the entire fund. Other holdings and assets account for the remaining allocation. Holdings can change after the reported date.")
