from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.analysis.explanations import analysis_insights
from ticker_analyzer.scoring import format_metric_value


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

def rank_results(results: dict[str, dict], sort_option: str) -> list[dict]:
    ranked = list(results.values())
    return sorted(
        ranked,
        key=lambda result: -1 if _comparison_score(result, sort_option) is None else _comparison_score(result, sort_option),
        reverse=True,
    )


def _comparison_score(result: dict, sort_option: str) -> float | None:
    if sort_option == "Overall":
        return result.get("overall_score")
    return result.get("tabs", {}).get(sort_option, {}).get("score")


def render_ranking(results: list[dict], sort_option: str) -> None:
    st.markdown(f"#### {sort_option} Ranking")
    if len(results) > 5:
        rows = [
            {"Rank": index + 1, "Ticker": result["ticker"], "Score": _comparison_score(result, sort_option)}
            for index, result in enumerate(results)
        ]
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
        )
        return
    columns = st.columns(len(results))
    for index, result in enumerate(results):
        score = _comparison_score(result, sort_option)
        score_text = "Missing" if score is None else f"{score:.1f}/100"
        columns[index].metric(f"#{index + 1} {result['ticker']}", score_text)


def render_company_cards(results: list[dict]) -> None:
    st.markdown("#### Company Cards")
    for result in results:
        score = result.get("overall_score")
        with st.container(border=True):
            st.markdown(f"##### {result['company_name']} ({result['ticker']})")
            columns = st.columns(7)
            columns[0].metric("Overall Score", "Missing" if score is None else f"{score:.1f}/100")
            columns[1].metric("Rating", result.get("rating", "Not Rated"))
            columns[2].metric("Price", format_company_price(result))
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
    rows = [
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
        for result in results
    ]
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

    st.subheader(f"{result['company_name']} ({result['ticker']})")
    cols = st.columns(6)
    cols[0].metric("Overall Score", score_label)
    cols[1].metric("Rating", result.get("rating", "Not Rated"))
    cols[2].metric("Current Price", format_company_price(result))
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

    render_score_explanation(result)

    if result.get("missing"):
        with st.expander("Missing data warnings", expanded=False):
            for item in result["missing"]:
                st.write(f"- {item}")


def render_score_explanation(result: dict) -> None:
    explanation = analysis_insights(result)
    st.markdown("#### Why this rating")
    columns = st.columns(3)
    sections = (
        ("Strongest signals", explanation["strongest"]),
        ("Weakest signals", explanation["weakest"]),
        ("What could improve it", explanation["improvements"]),
    )
    for column, (title, items) in zip(columns, sections, strict=True):
        with column:
            st.markdown(f"**{title}**")
            for item in items:
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
            "Value combines absolute multiples and cash yield with comparisons against the company's selected-range "
            "history, forward growth-adjusted valuation, and low-weight analyst context. A historical discount alone "
            "cannot produce a top score."
        )
        render_value_breakdown(tab_result)


def render_value_breakdown(tab_result: dict) -> None:
    groups = tab_result.get("group_breakdown", {}).get("groups", {})
    if not groups:
        return
    rows = [
        {
            "Value component": name.replace("_", " ").title(),
            "Score": details.get("score"),
            "Model weight": float(details.get("weight", 0)) * 100,
            "Available metrics": f"{details.get('available_metrics', 0)}/{details.get('total_metrics', 0)}",
        }
        for name, details in groups.items()
        if float(details.get("weight", 0)) > 0
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Score": st.column_config.NumberColumn(format="%.1f/100"),
            "Model weight": st.column_config.NumberColumn(format="%.0f%%"),
        },
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
