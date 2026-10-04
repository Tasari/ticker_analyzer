from __future__ import annotations

import hashlib
import math

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.providers.etf import EtfHoldings
from ticker_analyzer.providers.etf_joint import combine_etf_holdings
from ticker_analyzer.ui.etf_actions import selected_holding_tickers
from ticker_analyzer.ui.state import add_companies_to_analyzer


def render_joint(tickers: list[str], results: dict[str, EtfHoldings]) -> None:
    st.subheader("Joint")
    st.caption(
        "Combine the companies held by your ETFs. Each company's joint weight is the sum of its weight in each fund multiplied by that fund's allocation in the set."
    )
    equal = st.checkbox("Equal ETF weights", value=True, key="etf_joint_equal_weights")
    allocations = {}
    if equal:
        allocations = {ticker: 100 / len(tickers) for ticker in tickers}
        st.caption(f"Each of {len(tickers)} ETFs has a {100 / len(tickers):.2f}% allocation.")
    else:
        columns = st.columns(min(3, len(tickers)))
        for index, ticker in enumerate(tickers):
            allocations[ticker] = columns[index % len(columns)].number_input(
                f"Allocation for {ticker} (%)",
                min_value=0.0,
                max_value=100.0,
                value=100 / len(tickers),
                step=1.0,
                key=f"etf_joint_allocation_{ticker}",
            )
        total = sum(allocations.values())
        if not math.isclose(total, 100, abs_tol=0.01):
            st.info(f"Allocations must total 100%; current total is {total:.2f}%.")
            return
    joint = combine_etf_holdings(results, allocations)
    if joint.missing_funds:
        st.warning(
            "Holdings are unavailable or not loaded for: "
            + ", ".join(joint.missing_funds)
            + ". Their allocations remain unknown and are not redistributed to the other ETFs."
        )
    frame = joint.holdings
    if frame.empty:
        st.info("Load ETF holdings to create the joint set.")
        return
    known_weight = frame["Joint weight (%)"].sum()
    shared = frame[frame["ETF count"] > 1]
    metrics = st.columns(3)
    metrics[0].metric("Companies in joint set", len(frame))
    metrics[1].metric("Shared companies", len(shared))
    metrics[2].metric("Known share of the set", f"{known_weight:.2f}%")
    st.caption(
        "Joint uses every downloaded position, including positions beyond the per-ETF display limit. Holdings lists may be partial or from different dates. Weights are percentages of the entire set; unpublished positions and missing funds remain outside the known share."
    )
    st.caption(
        "Companies are combined by normalized name, including legal endings, share-class labels and confirmed name aliases. Listings keeps the original symbols. Ticker selects one available listing for analysis, preferring a US symbol when present; prices and share counts are not combined."
    )
    with st.expander("Fund allocations and sources"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ETF": ticker,
                        "Allocation (%)": weight,
                        "Known contribution (%)": joint.fund_coverage.get(ticker, 0.0),
                        "Holdings date": results[ticker].as_of or "not supplied" if ticker in results else "not loaded",
                        "Source": results[ticker].source if ticker in results else "not loaded",
                    }
                    for ticker, weight in allocations.items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    shared_only = st.checkbox("Show shared companies only", key="etf_joint_shared_only")
    displayed = shared if shared_only else frame
    if displayed.empty:
        st.info("No shared companies were found in the downloaded holdings.")
        return
    signature = hashlib.sha256(displayed.to_json(orient="split").encode()).hexdigest()[:16]
    event = st.dataframe(
        displayed,
        hide_index=True,
        width="stretch",
        key=f"etf_joint_table_{signature}",
        on_select="rerun",
        selection_mode="multi-row",
        column_config={"Joint weight (%)": st.column_config.NumberColumn(format="%.2f%%")},
    )
    selected = selected_holding_tickers(displayed, event.selection.rows, "Joint")
    st.button(
        "Add joint companies to Analyzer",
        type="primary",
        disabled=not selected,
        on_click=add_companies_to_analyzer,
        args=(selected,),
        key="etf_joint_add_companies",
    )
    figure = px.bar(displayed.head(15), x="Joint weight (%)", y="Company", orientation="h", text="Joint weight (%)")
    figure.update_traces(texttemplate="%{x:.2f}%", textposition="outside")
    figure.update_layout(
        yaxis={"autorange": "reversed"}, height=max(300, min(len(displayed), 15) * 32), margin={"t": 10}
    )
    st.plotly_chart(figure, width="stretch", key="etf_joint_chart")
