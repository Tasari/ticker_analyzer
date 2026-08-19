from __future__ import annotations

import pandas as pd
import streamlit as st

from ticker_analyzer.ranking import load_ranking
from ticker_analyzer.ui.actions import refresh_large_cap_ranking
from ticker_analyzer.ui.config_view import mutation_allowed


def render_large_cap_ranking() -> None:
    st.subheader("Large Cap Ranking — Scoring v5.1")
    refresh_allowed = mutation_allowed("ALLOW_RANKING_REFRESH")
    update_col, note_col = st.columns([1, 3])
    update_clicked = update_col.button(
        "Update Ranking",
        type="primary",
        help="Rebuild the US 1,000 plus up to 100 companies from every configured international market.",
        disabled=not refresh_allowed,
    )
    note_col.caption("The current snapshot stays visible until a complete replacement is ready.")
    if not refresh_allowed:
        note_col.caption("Ranking refresh is read-only in production unless explicitly enabled by an administrator.")
    if update_clicked:
        progress_bar = st.progress(0.0, text="Preparing the multi-market universe...")

        def update_progress(progress: dict) -> None:
            requested = int(progress.get("requested", 0) or 0)
            processed = int(
                progress.get(
                    "processed",
                    int(progress.get("analyzed", 0) or 0) + int(progress.get("failed", 0) or 0),
                )
                or 0
            )
            fraction = min(1.0, processed / requested) if requested else 0.0
            progress_bar.progress(
                fraction,
                text=(
                    f"Processed {processed:,}/{requested:,} companies "
                    f"({fraction * 100:.1f}%)"
                    if requested
                    else "Preparing the multi-market universe..."
                ),
            )

        with st.spinner("Updating the ranking; keep this page open..."):
            success, message, _metadata = refresh_large_cap_ranking(progress_callback=update_progress)
        if success:
            st.success(message)
            st.rerun()
        else:
            st.error(message)
    payload = load_ranking()
    metadata = payload.get("metadata", {})
    companies = payload.get("companies", [])
    errors = payload.get("errors", [])
    if not companies:
        st.info("Ranking data has not been generated yet.")
        return
    st.caption(
        f"{metadata.get('universe', 'Large-cap equities')} · generated {metadata.get('generated_at', 'unknown')} · "
        f"analyzed {metadata.get('analyzed', 0)}/{metadata.get('requested', 0)} · "
        f"scored {metadata.get('scored', 0)} · insufficient data {metadata.get('insufficient_data', 0)}"
    )
    if errors:
        with st.expander(f"Failed tickers ({len(errors)})", expanded=False):
            st.dataframe(pd.DataFrame(errors), hide_index=True, width="stretch")
    profiles = sorted({row.get("profile") for row in companies if row.get("profile")})
    ratings = sorted({row.get("rating") for row in companies if row.get("rating")})
    countries = sorted({row.get("country") for row in companies if row.get("country")})
    exchanges = sorted({row.get("exchange") for row in companies if row.get("exchange")})
    location_cols = st.columns(3)
    country = location_cols[0].selectbox("Country", ["All", *countries])
    exchange = location_cols[1].selectbox("Exchange", ["All", *exchanges])
    maximum_rows = location_cols[2].selectbox("Rows", [50, 100, 250, 500, 1000, 2500, 5000], index=1)
    score_cols = st.columns(3)
    profile = score_cols[0].selectbox("Profile", ["All", *profiles])
    rating = score_cols[1].selectbox("Rating", ["All", *ratings])
    minimum_quality = score_cols[2].slider("Minimum Data Quality", 0, 95, 0)
    filtered = [
        row
        for row in companies
        if (country == "All" or row.get("country") == country)
        and (exchange == "All" or row.get("exchange") == exchange)
        and (profile == "All" or row.get("profile") == profile)
        and (rating == "All" or row.get("rating") == rating)
        and float(row.get("data_quality", row.get("confidence")) or 0) >= minimum_quality
    ][:maximum_rows]
    table = pd.DataFrame(filtered)
    if table.empty:
        st.info("No companies match the selected country, exchange, profile, rating, and quality filters.")
        return
    columns = {
        "rank": "Rank",
        "ticker": "Ticker",
        "company_name": "Company",
        "country": "Country",
        "exchange": "Exchange",
        "market": "Market Pool",
        "market_cap": "Market Cap",
        "profile": "Profile",
        "overall_score": "Overall",
        "rating": "Rating",
        "rating_confidence": "Confidence",
        "data_quality": "Data Quality",
        "model_applicability": "Model Applicability",
        "growth_score": "Growth",
        "fundamentals_score": "Fundamentals",
        "value_score": "Value",
    }
    available = [name for name in columns if name in table.columns]
    table_event = st.dataframe(
        table[available].rename(columns=columns),
        hide_index=True,
        width="stretch",
        key="large_cap_ranking_table",
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "Market Cap": st.column_config.NumberColumn(format="$%.0f"),
            "Overall": st.column_config.NumberColumn(format="%.1f"),
            "Data Quality": st.column_config.NumberColumn(format="%.1f points"),
            "Model Applicability": st.column_config.NumberColumn(format="%.1f points"),
            "Growth": st.column_config.NumberColumn(format="%.1f"),
            "Fundamentals": st.column_config.NumberColumn(format="%.1f"),
            "Value": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    selected_rows = table_event.selection.rows
    selected_tickers = [filtered[index]["ticker"] for index in selected_rows]
    st.button(
        "Add selected companies to Analyzer",
        type="primary",
        disabled=not selected_tickers,
        help="Select one or more rows, then add those tickers to Stock Analyzer.",
        on_click=add_ranking_tickers_to_analyzer,
        args=(selected_tickers,),
    )
    st.caption("Ranking is a model-based screening tool, not investment advice. Missing tabs are never treated as neutral scores.")


def add_ranking_tickers_to_analyzer(tickers: list[str]) -> None:
    normalized_tickers = [ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()]
    if not normalized_tickers:
        return
    for ticker in normalized_tickers:
        if ticker not in st.session_state.selected_tickers:
            st.session_state.selected_tickers.append(ticker)
    st.session_state.analysis_results = {}
    st.session_state.analysis_errors = {}
    st.session_state.active_ticker = normalized_tickers[0]
    st.session_state.page = "Stock Analyzer"

