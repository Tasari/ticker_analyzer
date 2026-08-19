from __future__ import annotations

import html
import json
import os

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_searchbox import st_searchbox

from ticker_analyzer import format_metric_value, save_config
from ticker_analyzer.ranking import load_ranking
from ticker_analyzer.ui.actions import refresh_large_cap_ranking, search_tickers


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
    for ticker in list(st.session_state.selected_tickers):
        label_col, remove_col = st.columns([4, 1])
        label_col.write(ticker)
        if remove_col.button("X", key=f"remove_{ticker}", help=f"Remove {ticker}"):
            st.session_state.selected_tickers.remove(ticker)
            st.session_state.analysis_results.pop(ticker, None)
            st.session_state.analysis_errors.pop(ticker, None)
            if st.session_state.active_ticker == ticker:
                remaining = list(st.session_state.analysis_results)
                st.session_state.active_ticker = remaining[0] if remaining else None
            st.rerun()


def render_analysis_errors(errors: dict[str, str]) -> None:
    for ticker, message in errors.items():
        st.warning(f"{ticker}: {message}")


def render_company_analysis(result: dict) -> None:
    render_summary(result)
    render_tabs(result)


def render_multi_ticker_analysis(results: dict[str, dict]) -> None:
    summary_tab, details_tab = st.tabs(["Summary", "Company Details"])
    with summary_tab:
        render_comparison_summary(results)
    with details_tab:
        tickers = list(results)
        active_ticker = st.selectbox(
            "Company",
            tickers,
            index=tickers.index(st.session_state.active_ticker) if st.session_state.active_ticker in tickers else 0,
            format_func=lambda ticker: f"{ticker} | {results[ticker]['company_name']}",
        )
        st.session_state.active_ticker = active_ticker
        render_company_analysis(results[active_ticker])


def render_comparison_summary(results: dict[str, dict]) -> None:
    st.subheader("Comparison Summary")
    sort_option = st.selectbox(
        "Rank by",
        ["Overall", "Growth", "Fundamentals", "Value"],
        help="Rank selected stocks from highest to lowest score.",
    )
    ranked_results = rank_results(results, sort_option)
    render_ranking(ranked_results, sort_option)
    if len(ranked_results) <= 10:
        render_company_cards(ranked_results)
    else:
        st.caption("Company cards are omitted for large comparisons; select any company in Company Details.")
    render_comparison_table(ranked_results)
    if len(ranked_results) <= 25:
        with st.expander("Compare all metrics", expanded=False):
            render_metric_comparison(ranked_results)
    else:
        st.caption("The all-metrics matrix is available for comparisons of up to 25 companies.")


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


def rank_results(results: dict[str, dict], sort_option: str) -> list[dict]:
    ranked = list(results.values())
    if sort_option == "Overall":

        def score_for(result: dict) -> float | None:
            return result.get("overall_score")

    else:

        def score_for(result: dict) -> float | None:
            return result.get("tabs", {}).get(sort_option, {}).get("score")

    return sorted(
        ranked,
        key=lambda result: -1 if score_for(result) is None else score_for(result),
        reverse=True,
    )


def render_ranking(results: list[dict], sort_option: str) -> None:
    st.markdown(f"#### {sort_option} Ranking")
    if len(results) > 5:
        rows = []
        for index, result in enumerate(results):
            score = result.get("overall_score") if sort_option == "Overall" else result["tabs"][sort_option].get("score")
            rows.append({"Rank": index + 1, "Ticker": result["ticker"], "Score": score})
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
        )
        return
    columns = st.columns(len(results))
    for index, result in enumerate(results):
        score = result.get("overall_score") if sort_option == "Overall" else result["tabs"][sort_option].get("score")
        score_text = "Missing" if score is None else f"{score:.1f}/100"
        columns[index].metric(f"#{index + 1} {result['ticker']}", score_text)


def render_company_cards(results: list[dict]) -> None:
    st.markdown("#### Company Cards")
    for result in results:
        score = result.get("overall_score")
        price = result.get("current_price")
        currency = result.get("currency", "")
        price_text = "Missing" if price is None else f"{price:,.2f} {currency}".strip()
        with st.container(border=True):
            st.markdown(f"##### {result['company_name']} ({result['ticker']})")
            columns = st.columns(7)
            columns[0].metric("Overall Score", "Missing" if score is None else f"{score:.1f}/100")
            columns[1].metric("Rating", result.get("rating", "Not Rated"))
            columns[2].metric("Price", price_text)
            columns[3].metric("Profile", result.get("profile", "Industrial"))
            columns[4].metric("Growth", result["tabs"]["Growth"].get("rating", "Not Rated"))
            columns[5].metric("Fundamentals", result["tabs"]["Fundamentals"].get("rating", "Not Rated"))
            columns[6].metric("Value", result["tabs"]["Value"].get("rating", "Not Rated"))
            coverage = result.get("coverage", {})
            st.caption(
                f"Data Quality: {quality_label(data_quality_value(result))} "
                f"({data_quality_value(result):.0f}/100; coverage {coverage.get('percentage', 0):.0f}%)"
            )


def render_comparison_table(results: list[dict]) -> None:
    st.markdown("#### Score Comparison")
    rows = []
    for result in results:
        rows.append(
            {
                "Ticker": result["ticker"],
                "Company": result["company_name"],
                "Profile": result.get("profile", "Industrial"),
                "Price": format_company_price(result),
                "Overall Score": format_score(result.get("overall_score")),
                "Overall Rating": result.get("rating", "Not Rated"),
                "Data Quality": format_data_quality(result),
                "Growth": format_tab_summary(result, "Growth"),
                "Fundamentals": format_tab_summary(result, "Fundamentals"),
                "Value": format_tab_summary(result, "Value"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_metric_comparison(results: list[dict]) -> None:
    metric_order = []
    metrics_by_ticker = {}
    for result in results:
        ticker_metrics = {}
        for tab_name, tab_result in result["tabs"].items():
            for metric in tab_result.get("metrics", []):
                key = (tab_name, metric.id)
                if key not in metric_order:
                    metric_order.append(key)
                ticker_metrics[key] = metric
        metrics_by_ticker[result["ticker"]] = ticker_metrics

    rows = []
    for tab_name, metric_id in metric_order:
        row = {"Category": tab_name, "Metric": metric_id}
        metric_name = metric_id
        for ticker, ticker_metrics in metrics_by_ticker.items():
            metric = ticker_metrics.get((tab_name, metric_id))
            if metric is None:
                row[ticker] = "Missing"
                continue
            metric_name = metric.name
            score = "" if metric.score is None else f" | score {metric.score:.1f}"
            row[ticker] = f"{format_metric_value(metric.value, metric.unit)} | {metric.status}{score}"
        row["Metric"] = metric_name
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def format_company_price(result: dict) -> str:
    price = result.get("current_price")
    currency = result.get("currency", "")
    return "Missing" if price is None else f"{price:,.2f} {currency}".strip()


def format_score(score: float | None) -> str:
    return "Missing" if score is None else f"{score:.1f}/100"


def format_tab_summary(result: dict, tab_name: str) -> str:
    tab_result = result.get("tabs", {}).get(tab_name, {})
    return (
        f"{format_score(tab_result.get('score'))} | {tab_result.get('rating', 'Not Rated')} | "
        f"{format_coverage(tab_result.get('coverage', {}))}"
    )


def format_coverage(coverage: dict) -> str:
    percentage = coverage.get("percentage")
    if percentage is None:
        return "Unknown"
    return f"{coverage.get('confidence', 'Unknown')} ({percentage:.0f}%)"


def quality_label(value: float) -> str:
    if value >= 80:
        return "High"
    if value >= 60:
        return "Medium"
    return "Low"


def data_quality_value(result: dict) -> float:
    return float(result.get("data_quality", result.get("confidence", 0)) or 0)


def format_data_quality(result: dict) -> str:
    value = data_quality_value(result)
    return f"{quality_label(value)} ({value:.0f}/100)"


def render_summary(result: dict) -> None:
    score = result.get("overall_score")
    score_label = "Not Rated" if score is None else f"{score:.1f}/100"
    current_price = result.get("current_price")
    currency = result.get("currency", "")
    price_label = "Missing" if current_price is None else f"{current_price:,.2f} {currency}".strip()

    st.subheader(f"{result['company_name']} ({result['ticker']})")
    cols = st.columns(6)
    cols[0].metric("Overall Score", score_label)
    cols[1].metric("Rating", result.get("rating", "Not Rated"))
    cols[2].metric("Current Price", price_label)
    cols[3].metric("Analysis Profile", result.get("profile", "Industrial"))
    cols[4].metric("Available Tabs", sum(1 for tab in result["tabs"].values() if tab["score"] is not None))
    cols[5].metric("Data Quality", format_data_quality(result))
    st.caption(
        f"Rating confidence: {result.get('rating_confidence', 'Unknown')} · "
        f"Model applicability: {float(result.get('model_applicability', 100) or 0):.0f}/100"
    )
    if result.get("rating_caps"):
        st.caption("Active rating caps: " + ", ".join(result["rating_caps"]))
    st.caption("Scores are model-based comparative indicators, not guarantees or investment advice.")

    rating_cols = st.columns(3)
    for index, tab_name in enumerate(["Growth", "Fundamentals", "Value"]):
        tab_result = result["tabs"].get(tab_name, {})
        tab_score = tab_result.get("score")
        score_text = "" if tab_score is None else f"{tab_score:.1f}/100"
        tab_range = result.get("ranges", {}).get(tab_name, "")
        label = f"{tab_name} Rating" if not tab_range else f"{tab_name} Rating ({tab_range})"
        coverage = format_coverage(tab_result.get("coverage", {}))
        rating_cols[index].metric(label, tab_result.get("rating", "Not Rated"), score_text)
        rating_cols[index].caption(f"Metric coverage: {coverage}")

    if result.get("missing"):
        with st.expander("Missing data warnings", expanded=False):
            for item in result["missing"]:
                st.write(f"- {item}")


def render_tabs(result: dict) -> None:
    growth_tab, fundamentals_tab, value_tab = st.tabs(["Growth", "Fundamentals", "Value"])
    tab_map = {
        "Growth": growth_tab,
        "Fundamentals": fundamentals_tab,
        "Value": value_tab,
    }
    for name, tab in tab_map.items():
        with tab:
            render_tab(name, result["tabs"].get(name, {}), result.get("charts", {}))


def render_tab(name: str, tab_result: dict, charts: dict) -> None:
    score = tab_result.get("score")
    score_text = "Missing" if score is None else f"{score:.1f}/100"
    score_col, rating_col, coverage_col = st.columns(3)
    score_col.metric(f"{name} Score", score_text)
    rating_col.metric(f"{name} Rating", tab_result.get("rating", "Not Rated"))
    coverage_col.metric("Metric Coverage", format_coverage(tab_result.get("coverage", {})))
    metrics = tab_result.get("metrics", [])
    render_metrics_table(metrics)

    if name == "Growth":
        render_line_chart(charts.get("financials"), "Annual Financial Trends")
        render_line_chart(charts.get("prices"), "Selected Price Range")
    elif name == "Fundamentals":
        render_line_chart(charts.get("fundamentals"), "Debt and Assets")
    elif name == "Value":
        st.info(
            "Value metrics compare current multiples and upside signals against approximate historical medians "
            "when enough data is available."
        )


def render_line_chart(frame: pd.DataFrame | None, title: str) -> None:
    if frame is None or frame.empty:
        st.warning(f"{title}: not enough data to chart.")
        return
    chart_frame = frame.copy()
    chart_frame.index = chart_frame.index.astype(str)
    chart_frame = chart_frame.reset_index(names="Date")
    melted = chart_frame.melt(id_vars="Date", var_name="Metric", value_name="Value")
    fig = px.line(melted, x="Date", y="Value", color="Metric", markers=True, title=title)
    st.plotly_chart(fig, width="stretch")


def render_metrics_table(metrics: list) -> None:
    rows = []
    for metric in metrics:
        score = "" if metric.score is None else f"{metric.score:.1f}"
        tooltip = html.escape(metric.description or "No description available.", quote=True)
        rows.append(
            "<tr>"
            f"<td>{html.escape(metric.name)}</td>"
            f"<td><span class='metric-info' title='{tooltip}'>Info</span></td>"
            f"<td>{html.escape(format_metric_value(metric.value, metric.unit))}</td>"
            f"<td>{html.escape(score)}</td>"
            f"<td>{metric.weight:g}</td>"
            f"<td>{html.escape(metric.status)}</td>"
            f"<td>{html.escape(metric.note)}</td>"
            "</tr>"
        )

    st.markdown(
        """
        <style>
        .metrics-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }
        .metrics-table th,
        .metrics-table td {
            border: 1px solid rgba(128, 128, 128, 0.28);
            padding: 0.55rem 0.6rem;
            text-align: left;
            vertical-align: top;
        }
        .metrics-table th {
            background: rgba(128, 128, 128, 0.12);
            font-weight: 600;
        }
        .metric-info {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 2.4rem;
            border: 1px solid rgba(128, 128, 128, 0.45);
            border-radius: 999px;
            padding: 0.1rem 0.4rem;
            cursor: help;
            font-size: 0.78rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        "<table class='metrics-table'>"
        "<thead><tr>"
        "<th>Metric</th><th>Info</th><th>Value</th><th>Score</th><th>Weight</th><th>Status</th><th>Note</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>",
        unsafe_allow_html=True,
    )


def render_config_editor(config: dict) -> None:
    with st.expander("Scoring Settings", expanded=False):
        write_allowed = mutation_allowed("ALLOW_CONFIG_WRITE")
        st.write("Edit the JSON configuration, then save and analyze again.")
        edited = st.text_area(
            "metrics_config.json",
            value=json.dumps(config, indent=2),
            height=420,
            label_visibility="collapsed",
            disabled=not write_allowed,
        )
        cols = st.columns(2)
        if cols[0].button("Save settings", width="stretch", disabled=not write_allowed):
            try:
                parsed = json.loads(edited)
                save_config(parsed)
                st.success("Settings saved.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save settings: {exc}")
        if cols[1].button("Reload settings", width="stretch"):
            st.rerun()


def mutation_allowed(setting: str) -> bool:
    if os.getenv("APP_MODE", "local").strip().lower() != "production":
        return True
    return os.getenv(setting, "").strip().lower() in {"1", "true", "yes", "on"}
