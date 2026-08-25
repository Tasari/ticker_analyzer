from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from ticker_analyzer.ranking import (
    RankingSnapshotError,
    export_ranking,
    import_ranking,
    load_ranking,
    save_ranking,
)
from ticker_analyzer.ranking_filters import RankingFilters, filter_ranking_companies
from ticker_analyzer.ranking_quality import build_ranking_quality_report
from ticker_analyzer.ui.config_view import mutation_allowed
from ticker_analyzer.ui.ranking_actions import refresh_large_cap_ranking
from ticker_analyzer.ui.state import add_tickers_to_state


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
    _render_snapshot_transfer(payload)
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
    with st.expander("Ranking filters", expanded=True):
        search_cols = st.columns([3, 1, 1])
        query = search_cols[0].text_input(
            "Search",
            placeholder="Ticker, company, or industry",
            key="ranking_filter_query",
        )
        maximum_rows = search_cols[1].selectbox(
            "Rows",
            [50, 100, 250, 500, 1000, 2500, 5000],
            index=1,
            key="ranking_filter_rows",
        )
        include_unscored = search_cols[2].checkbox(
            "Include unscored",
            value=True,
            key="ranking_filter_include_unscored",
        )

        location_cols = st.columns(4)
        countries = location_cols[0].multiselect(
            "Country",
            _filter_options(companies, "country"),
            key="ranking_filter_countries",
        )
        markets = location_cols[1].multiselect(
            "Market Pool",
            _filter_options(companies, "market"),
            key="ranking_filter_markets",
        )
        exchanges = location_cols[2].multiselect(
            "Exchange",
            _filter_options(companies, "exchange"),
            key="ranking_filter_exchanges",
        )
        sectors = location_cols[3].multiselect(
            "Sector",
            _filter_options(companies, "sector"),
            key="ranking_filter_sectors",
        )

        classification_cols = st.columns(3)
        profiles = classification_cols[0].multiselect(
            "Profile",
            _filter_options(companies, "profile"),
            key="ranking_filter_profiles",
        )
        ratings = classification_cols[1].multiselect(
            "Rating",
            _filter_options(companies, "rating"),
            key="ranking_filter_ratings",
        )
        confidences = classification_cols[2].multiselect(
            "Confidence",
            _filter_options(companies, "rating_confidence"),
            key="ranking_filter_confidences",
        )

        score_cols = st.columns(3)
        overall_score_range = score_cols[0].slider(
            "Overall Score",
            0,
            100,
            (0, 100),
            key="ranking_filter_overall_score",
        )
        minimum_quality = score_cols[1].slider(
            "Minimum Data Quality",
            0,
            100,
            0,
            key="ranking_filter_quality",
        )
        minimum_market_cap_billions = score_cols[2].number_input(
            "Minimum Market Cap (B)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="ranking_filter_market_cap",
        )

        tab_score_cols = st.columns(3)
        minimum_growth = tab_score_cols[0].slider(
            "Minimum Growth Score", 0, 100, 0, key="ranking_filter_growth"
        )
        minimum_fundamentals = tab_score_cols[1].slider(
            "Minimum Fundamentals Score", 0, 100, 0, key="ranking_filter_fundamentals"
        )
        minimum_value = tab_score_cols[2].slider(
            "Minimum Value Score", 0, 100, 0, key="ranking_filter_value"
        )

    matching = filter_ranking_companies(
        companies,
        RankingFilters(
            query=query,
            countries=tuple(countries),
            markets=tuple(markets),
            exchanges=tuple(exchanges),
            sectors=tuple(sectors),
            profiles=tuple(profiles),
            ratings=tuple(ratings),
            confidences=tuple(confidences),
            overall_score_range=overall_score_range,
            minimum_growth=minimum_growth,
            minimum_fundamentals=minimum_fundamentals,
            minimum_value=minimum_value,
            minimum_quality=minimum_quality,
            minimum_market_cap=minimum_market_cap_billions * 1_000_000_000,
            include_unscored=include_unscored,
        ),
    )
    filtered = matching[:maximum_rows]
    st.caption(
        f"Showing {len(filtered):,} of {len(matching):,} matching companies "
        f"({len(companies):,} in snapshot)."
    )
    table = pd.DataFrame(filtered)
    if table.empty:
        st.info("No companies match the selected ranking filters.")
        _render_quality_report(payload)
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
    _render_quality_report(payload)


def _filter_options(companies: list[dict], field: str) -> list[str]:
    return sorted({str(row[field]) for row in companies if row.get(field)})


def _render_snapshot_transfer(payload: dict) -> None:
    with st.expander("Ranking backup: download / import", expanded=False):
        st.caption(
            "Download the current snapshot before a Streamlit restart or import it later without rebuilding "
            "the full universe. Imported JSON is validated before replacement."
        )
        has_snapshot = bool(payload.get("companies"))
        export_bytes = export_ranking(payload) if has_snapshot else b""
        st.download_button(
            "Download ranking JSON",
            data=export_bytes,
            file_name=ranking_export_filename(payload.get("metadata", {})),
            mime="application/json",
            disabled=not has_snapshot,
            help="Download a timestamped backup of the current ranking.",
            width="stretch",
        )
        if not has_snapshot:
            st.caption("Generate or import a ranking before downloading its backup.")
        import_allowed = mutation_allowed("ALLOW_RANKING_IMPORT")
        uploaded = st.file_uploader(
            "Import ranking JSON",
            type=["json"],
            accept_multiple_files=False,
            disabled=not import_allowed,
            key="ranking_snapshot_import",
        )
        confirm = st.checkbox(
            "Replace the current ranking with this validated snapshot",
            disabled=uploaded is None or not import_allowed,
            key="ranking_snapshot_import_confirm",
        )
        if st.button(
            "Import snapshot",
            disabled=uploaded is None or not confirm or not import_allowed,
            width="stretch",
        ):
            try:
                imported = import_ranking(uploaded.getvalue())
                imported["metadata"]["quality_report"] = build_ranking_quality_report(imported, payload)
                save_ranking(imported)
            except (RankingSnapshotError, OSError, ValueError) as exc:
                st.error(f"Ranking import failed: {exc}")
            else:
                st.success(f"Imported {len(imported.get('companies', [])):,} ranking rows.")
                st.rerun()
        if not import_allowed:
            st.caption("Set ALLOW_RANKING_IMPORT=true to enable imports in production mode.")


def _render_quality_report(payload: dict) -> None:
    metadata = payload.get("metadata", {})
    report = metadata.get("quality_report") or build_ranking_quality_report(payload)
    with st.expander("Update quality report", expanded=bool(report.get("warnings"))):
        summary = report.get("summary", {})
        metrics = st.columns(4)
        metrics[0].metric("Processed", f"{summary.get('processed', 0):,}/{summary.get('requested', 0):,}")
        metrics[1].metric("Scored", f"{summary.get('scored', 0):,}")
        metrics[2].metric("Failed", f"{summary.get('failed', 0):,}")
        success_rate = summary.get("success_rate")
        metrics[3].metric("Success rate", "N/A" if success_rate is None else f"{success_rate:.1%}")
        for warning in report.get("warnings", []):
            st.warning(warning)
        markets = report.get("markets", [])
        if markets:
            market_frame = pd.DataFrame(markets).rename(
                columns={
                    "market": "Market",
                    "expected": "Universe",
                    "analyzed": "Analyzed",
                    "scored": "Scored",
                    "coverage": "Coverage",
                }
            )
            market_frame["Coverage"] = market_frame["Coverage"].map(
                lambda value: "N/A" if pd.isna(value) else f"{value:.1%}"
            )
            st.dataframe(market_frame, hide_index=True, width="stretch")
        comparison = report.get("comparison", {})
        if comparison.get("previous_available"):
            mean_change = comparison.get("mean_absolute_score_change")
            st.caption(
                f"Versus previous snapshot: {comparison.get('added', 0):,} added, "
                f"{comparison.get('removed', 0):,} removed, "
                f"{comparison.get('rating_change_count', len(comparison.get('rating_changes', []))):,} "
                "rating changes, mean absolute "
                f"score change {'N/A' if mean_change is None else f'{mean_change:.2f}'}."
            )
            if comparison.get("largest_rank_moves"):
                st.dataframe(pd.DataFrame(comparison["largest_rank_moves"]), hide_index=True, width="stretch")
        categories = report.get("error_categories", {})
        if categories:
            st.caption("Failure categories: " + ", ".join(f"{name}: {count}" for name, count in categories.items()))


def ranking_export_filename(metadata: dict) -> str:
    raw_timestamp = str(metadata.get("generated_at") or "").strip()
    try:
        generated_at = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return "large_cap_ranking_snapshot.json"
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    generated_at = generated_at.astimezone(UTC)
    return f"large_cap_ranking_{generated_at:%Y-%m-%d_%H-%M-%S}_UTC.json"


def add_ranking_tickers_to_analyzer(tickers: list[str]) -> None:
    added = add_tickers_to_state(st.session_state, tickers)
    if not added:
        return
    st.session_state.page = "Stock Analyzer"
