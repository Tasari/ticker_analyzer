from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from ticker_analyzer.config import load_config
from ticker_analyzer.ranking import (
    DEFAULT_RANKING_PATH,
    RankingSnapshotError,
    load_ranking,
)
from ticker_analyzer.ranking.bundle import (
    RANKING_LABELS,
    available_ranking_snapshots,
    build_rankings_archive,
    import_rankings_archive,
)
from ticker_analyzer.ranking.filters import RankingFilters, filter_ranking_companies
from ticker_analyzer.ranking.quality import build_ranking_quality_report, ranking_compatibility
from ticker_analyzer.ui.config_view import mutation_allowed
from ticker_analyzer.ui.market_ranking_view import render_crypto_ranking, render_etf_ranking
from ticker_analyzer.ui.ranking_actions import ranking_refresh_is_running, refresh_large_cap_ranking
from ticker_analyzer.ui.state import add_tickers_to_state


def render_large_cap_ranking() -> None:
    _render_all_ranking_controls()
    stocks_tab, etfs_tab, crypto_tab = st.tabs(["Stocks", "ETFs", "Crypto"])
    with stocks_tab:
        _render_stock_ranking()
    with etfs_tab:
        render_etf_ranking()
    with crypto_tab:
        render_crypto_ranking()


def _render_all_ranking_controls() -> None:
    refresh_allowed = mutation_allowed("ALLOW_RANKING_REFRESH")
    restart_confirmed = bool(st.session_state.pop("ranking_restart_confirmed", False))
    restart_scope = st.session_state.pop("ranking_restart_scope", "all")
    refresh_running = ranking_refresh_is_running()
    update_col, download_col, import_col, note_col = st.columns([1, 1, 1, 2])
    update_clicked = update_col.button(
        "Update all rankings",
        type="primary",
        disabled=not refresh_allowed,
        help="Refresh Stocks, ETFs, and Crypto in one operation.",
    )
    scope = "all" if update_clicked else None
    with update_col:
        for key, label in (("stocks", "Stocks"), ("etfs", "ETFs"), ("crypto", "Crypto")):
            with st.expander(label, expanded=False):
                if st.button(
                    f"Update {label} only",
                    key=f"ranking_update_{key}",
                    disabled=not refresh_allowed,
                    width="stretch",
                    help=f"Refresh only {label}; the other ranking snapshots keep their current data.",
                ):
                    scope = key
    snapshot_count = available_ranking_snapshots()
    download_col.download_button(
        "Download all rankings",
        data=build_rankings_archive() if snapshot_count else b"",
        file_name=f"all_rankings_{datetime.now(UTC):%Y-%m-%d_%H-%M-%S}_UTC.zip",
        mime="application/zip",
        disabled=not snapshot_count,
        help=f"Download {snapshot_count}/3 available ranking snapshots as one ZIP archive.",
    )
    with import_col:
        _render_archive_import(refresh_running=refresh_running)
    message = st.session_state.pop("ranking_archive_import_message", None)
    if message:
        st.success(message)
    note_col.caption("Updates replace each snapshot only after that ranking completes successfully.")
    if not refresh_allowed:
        note_col.caption("Ranking refresh is read-only in production unless explicitly enabled by an administrator.")
    if refresh_running:
        _render_running_stock_progress()
    outcome_panel = st.empty()
    with outcome_panel.container():
        _render_refresh_outcomes(st.session_state.get("ranking_update_outcomes", []))
    if scope in {"all", "stocks"} and refresh_running:
        _confirm_ranking_restart(scope)
        return
    if restart_confirmed:
        scope = restart_scope
    if scope is None or not refresh_allowed:
        return

    # Persist small status records so completed stages stay visible after reruns.
    outcomes: list[tuple[str, bool, str]] = []
    st.session_state["ranking_update_outcomes"] = outcomes
    outcome_panel.empty()
    progress_bar = st.progress(0.0, text="Preparing the ranking update...")

    def update_progress(progress: dict) -> None:
        requested = int(progress.get("requested", 0) or 0)
        processed = int(
            progress.get(
                "processed",
                int(progress.get("analyzed", 0) or 0) + int(progress.get("failed", 0) or 0),
            )
            or 0
        )
        stock_fraction = min(1.0, processed / requested) if requested else 0.0
        progress_bar.progress(
            0.15 + stock_fraction * 0.85 if scope == "all" else stock_fraction,
            text=(f"Stocks: {processed:,}/{requested:,} processed" if requested else "Preparing the stock universe..."),
        )

    with st.spinner("Updating selected rankings; keep this page open..."):
        from ticker_analyzer.ranking.assets import refresh_crypto_ranking, refresh_etf_ranking

        # Publish the short asset refreshes before the long stock build. They
        # must not depend on that build finishing or the page remaining open.
        for label, fraction, refresh in (
            ("ETFs", 0.07, refresh_etf_ranking),
            ("Crypto", 0.15, refresh_crypto_ranking),
        ):
            if scope != "all" and scope != label.casefold():
                continue
            progress_bar.progress(fraction if scope == "all" else 0.0, text=f"Updating {label}...")
            try:
                payload = refresh()
            except Exception as exc:
                outcomes.append((label, False, f"{type(exc).__name__}: {exc}"))
            else:
                count = len(payload.get("companies", []))
                errors = len(payload.get("errors", []))
                message = f"{count:,} instruments saved"
                if errors:
                    message += f"; {errors:,} provider errors (partial coverage)"
                outcomes.append((label, True, message))
            st.session_state["ranking_update_outcomes"] = outcomes.copy()
            with outcome_panel.container():
                _render_refresh_outcomes(outcomes)

        if scope in {"all", "stocks"}:
            progress_bar.progress(0.15 if scope == "all" else 0.0, text="Preparing the stock universe...")
            try:
                success, message, _metadata = refresh_large_cap_ranking(
                    progress_callback=update_progress,
                    restart_running=restart_confirmed,
                )
            except Exception as exc:
                outcomes.append(("Stocks", False, f"{type(exc).__name__}: {exc}"))
            else:
                outcomes.append(("Stocks", success, message))
            st.session_state["ranking_update_outcomes"] = outcomes.copy()
    progress_bar.progress(1.0, text="Ranking update finished.")
    with outcome_panel.container():
        _render_refresh_outcomes(outcomes)
    if all(success for _, success, _ in outcomes):
        st.rerun()


def _render_refresh_outcomes(outcomes: list[tuple[str, bool, str]]) -> None:
    failures = [(label, message) for label, success, message in outcomes if not success]
    successes = [(label, message) for label, success, message in outcomes if success]
    if successes:
        st.success("Updated: " + "; ".join(f"{label} — {message}" for label, message in successes))
    if failures:
        st.error("Failed: " + "; ".join(f"{label} — {message}" for label, message in failures))


@st.dialog("Restart the ranking update?")
def _confirm_ranking_restart(scope: str = "all") -> None:
    target = "all rankings" if scope == "all" else "Stocks"
    st.warning(
        "A ranking update is already running. Restarting will stop that process, "
        f"discard its unfinished checkpoint, and begin {target} again from 0%."
    )
    confirm_col, keep_col = st.columns(2)
    confirm_col.button(
        "Yes, restart",
        type="primary",
        width="stretch",
        on_click=_mark_ranking_restart_confirmed,
        args=(scope,),
    )
    keep_col.button("No, keep running", width="stretch")


def _mark_ranking_restart_confirmed(scope: str = "all") -> None:
    st.session_state["ranking_restart_confirmed"] = True
    st.session_state["ranking_restart_scope"] = scope


@st.fragment(run_every=1)
def _render_running_stock_progress() -> None:
    if not ranking_refresh_is_running():
        st.rerun()
        return
    checkpoint_path = DEFAULT_RANKING_PATH.with_suffix(".refresh.json")
    metadata = load_ranking(checkpoint_path).get("metadata", {})
    requested = int(metadata.get("requested", 0) or 0)
    processed = int(
        metadata.get(
            "processed",
            int(metadata.get("analyzed", 0) or 0) + int(metadata.get("failed", 0) or 0),
        )
        or 0
    )
    fraction = min(1.0, processed / requested) if requested else 0.0
    text = (
        f"Stocks update is running: {processed:,}/{requested:,} processed ({fraction:.1%})"
        if requested
        else "Stocks update is running: preparing the exchange universe..."
    )
    st.progress(fraction, text=text)
    st.caption("The update is still active. Click Update all rankings if you want to stop it and restart from 0%.")


def _load_stock_ranking_for_display() -> tuple[dict, bool]:
    snapshot = load_ranking()
    if snapshot.get("companies"):
        return snapshot, False
    checkpoint_path = DEFAULT_RANKING_PATH.with_suffix(".refresh.json")
    checkpoint = load_ranking(checkpoint_path)
    if checkpoint.get("companies"):
        return checkpoint, True
    return snapshot, False


def _render_stock_ranking() -> None:
    st.subheader("Large Cap Ranking — Scoring v5.2")
    payload, is_checkpoint = _load_stock_ranking_for_display()
    metadata = payload.get("metadata", {})
    companies = payload.get("companies", [])
    errors = payload.get("errors", [])
    if not companies:
        st.info("Ranking data has not been generated yet. Use Update all rankings to start it.")
        return
    _render_ranking_compatibility(payload)
    if is_checkpoint:
        processed = int(metadata.get("processed", len(companies) + len(errors)) or 0)
        requested = int(metadata.get("requested", 0) or 0)
        st.warning(
            f"Showing a saved in-progress Stocks checkpoint ({processed:,}/{requested:,}). "
            "Use Update all rankings to resume it, or confirm Restart to discard it."
        )
    for warning in metadata.get("universe_warnings", []):
        st.warning(f"Stock universe: {warning}")
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
            "Minimum Market Cap (B USD)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key="ranking_filter_market_cap",
        )

        tab_score_cols = st.columns(3)
        minimum_growth = tab_score_cols[0].slider("Minimum Growth Score", 0, 100, 0, key="ranking_filter_growth")
        minimum_fundamentals = tab_score_cols[1].slider(
            "Minimum Fundamentals Score", 0, 100, 0, key="ranking_filter_fundamentals"
        )
        minimum_value = tab_score_cols[2].slider("Minimum Value Score", 0, 100, 0, key="ranking_filter_value")

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
    st.caption(f"Showing {len(filtered):,} of {len(matching):,} matching companies ({len(companies):,} in snapshot).")
    table = pd.DataFrame(filtered)
    if any(row.get("market_cap_currency") != "USD" for row in companies):
        st.caption(
            "This snapshot contains unlabelled capitalization amounts. Update it to enable reliable USD capitalization filtering."
        )
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
        "market": "Market",
        "currency": "Currency",
        "price": "Price",
        "market_cap": "Market Cap",
        "market_cap_currency": "Cap Currency",
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
            "Market Cap": st.column_config.NumberColumn(
                format="%.0f",
                help="USD for new snapshots; see Cap Currency. Regenerate legacy snapshots without a currency label.",
            ),
            "Price": st.column_config.NumberColumn(format="%.2f"),
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
    st.caption(
        "Ranking is a model-based screening tool, not investment advice. Missing tabs are never treated as neutral scores."
    )
    _render_quality_report(payload)


def _filter_options(companies: list[dict], field: str) -> list[str]:
    return sorted({str(row[field]) for row in companies if row.get(field)})


def _render_archive_import(*, refresh_running: bool) -> None:
    import_allowed = mutation_allowed("ALLOW_RANKING_IMPORT")
    with st.popover("Load rankings", disabled=not import_allowed or refresh_running):
        st.caption(
            "Upload a ZIP from Download all rankings. Stocks, ETFs and Crypto in the archive replace their saved rankings; absent tabs keep their current data."
        )
        uploaded = st.file_uploader(
            "Ranking ZIP",
            type=["zip"],
            accept_multiple_files=False,
            disabled=not import_allowed or refresh_running,
            max_upload_size=50,
            key="ranking_archive_import",
        )
        import_clicked = st.button(
            "Import ZIP",
            disabled=uploaded is None or not import_allowed or refresh_running,
            width="stretch",
        )
        if import_clicked and uploaded is not None and import_allowed and not refresh_running:
            try:
                imported = import_rankings_archive(uploaded.getvalue())
            except (RankingSnapshotError, OSError, ValueError) as exc:
                st.error(f"Ranking import failed: {exc}")
            else:
                summary = "; ".join(
                    f"{RANKING_LABELS[name]} — {len(snapshot['companies']):,} rows"
                    for name, snapshot in imported.items()
                )
                st.session_state["ranking_archive_import_message"] = f"Imported rankings: {summary}."
                st.rerun()
    if not import_allowed:
        st.caption("Set ALLOW_RANKING_IMPORT=true to enable imports in production mode.")
    elif refresh_running:
        st.caption("Load rankings after the current update finishes.")


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


def _render_ranking_compatibility(payload: dict) -> None:
    compatibility = ranking_compatibility(payload, load_config())
    metadata = payload.get("metadata", {})
    for warning in compatibility["warnings"]:
        st.warning(warning)
    with st.expander("Ranking calculation versions", expanded=False):
        st.caption(
            "Matching versions do not guarantee matching scores: fetch dates, ranges and available data also matter."
        )
        if compatibility["differences"]:
            st.dataframe(pd.DataFrame(compatibility["differences"]).astype(str), hide_index=True, width="stretch")
        elif compatibility["compatible"]:
            st.success("Calculation versions and configuration match the current application.")
        st.caption(
            f"Snapshot range: {metadata.get('ranges', 'Unknown')} · data as of {metadata.get('data_as_of', 'Unknown')}"
        )


def add_ranking_tickers_to_analyzer(tickers: list[str]) -> None:
    added = add_tickers_to_state(st.session_state, tickers)
    if not added:
        return
    st.session_state.page = "Stock Analyzer"
