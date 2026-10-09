from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ticker_analyzer.analysis.explanations import analysis_insights
from ticker_analyzer.scoring import format_metric_value
from ticker_analyzer.ui.fair_value_view import render_fair_value


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
            st.caption(format_market_identity(result))
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
            "Market": format_market_identity(result),
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


def format_market_identity(result: dict) -> str:
    parts = []
    for value in (result.get("market") or result.get("exchange"), result.get("country")):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return " · ".join(parts) or "Market unavailable"


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
    st.caption(format_market_identity(result))
    basis = result.get("valuation_basis", {})
    if basis:
        with st.expander("Valuation currencies and share units", expanded=False):
            st.write({
                "Price currency": basis.get("quote_currency"),
                "Statement currency": basis.get("reporting_currency") or "Unknown",
                "Ordinary shares per listed unit": basis.get("ordinary_shares_per_receipt"),
                "Quote-to-statement FX": basis.get("quote_to_reporting_fx"),
                "Share conversion source": basis.get("share_ratio_source"),
            })
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
    render_valuation_evidence(result)

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


def render_valuation_evidence(result: dict) -> None:
    rows, warnings = [], []
    for metric_id, label in (("price_to_sales_current", "P/S"), ("pe_current", "P/E"),
                             ("pb_current", "P/B"), ("ev_ebitda_current", "EV/EBITDA")):
        raw = result.get("raw", {}).get(metric_id, {})
        evidence = raw.get("valuation", {})
        if not evidence:
            continue
        rows.append({
            "Metric": label, "Used multiple": raw.get("value"), "Source": evidence["source"],
            "Statement period": evidence["period"], "Yahoo multiple": evidence.get("reported_multiple"),
            "Difference (%)": evidence.get("difference_pct"),
            "Historical months": f"{evidence['history_observations']}/{evidence['history_expected']}",
        })
        warnings.extend(f"{label}: {warning}" for warning in evidence.get("warnings", []))
    if not rows:
        return
    if warnings:
        st.caption(f"Valuation checks: {len(warnings)} notes — see Valuation evidence below.")
    with st.expander("Valuation evidence", expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Multiples are dimensionless. Historical months count usable observations, not independent reports. "
                   "Differences of at least 25% are review flags, not proof that either value is correct. "
                   "These checks do not change rating thresholds.")
        for warning in warnings:
            st.warning(warning)


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
    graphs_tab, growth_tab, fundamentals_tab, value_tab, fair_value_tab = st.tabs(
        ["Graphs", "Growth", "Fundamentals", "Value", "Fair Value"]
    )
    with graphs_tab:
        charts = result.get("charts", {})
        render_financial_trends(charts)
        render_ps_trends(charts)
        render_line_chart(charts.get("prices"), "Selected Price Range")
        render_line_chart(charts.get("fundamentals"), "Debt and Assets")
        fair_value_chart = st.container()
    tab_map = {
        "Growth": growth_tab,
        "Fundamentals": fundamentals_tab,
        "Value": value_tab,
    }
    for name, tab in tab_map.items():
        with tab:
            render_tab(name, result["tabs"].get(name, {}), result.get("charts", {}))
            if name == "Value" and result["tabs"].get(name, {}).get("score") is None:
                for diagnostic in result.get("diagnostics", []):
                    if diagnostic.get("source") == "valuation basis":
                        st.warning(diagnostic["message"])
                failures = [
                    item for item in result.get("diagnostics", [])
                    if item.get("kind") in {"network_error", "provider_error"}
                ]
                if failures:
                    st.warning("Market-data downloads failed. Click Analyze to retry with fresh data.")
                    with st.expander("Data download failures", expanded=False):
                        for failure in failures:
                            st.text(f"{failure.get('source', 'Provider')}: {failure.get('message', 'Download failed')}")
    with fair_value_tab:
        render_fair_value(result, chart_container=fair_value_chart)


def render_tab(name: str, tab_result: dict, charts: dict) -> None:
    score = tab_result.get("score")
    score_text = "Missing" if score is None else f"{score:.1f}/100"
    score_col, rating_col, coverage_col = st.columns(3)
    score_col.metric(f"{name} Score", score_text)
    rating_col.metric(f"{name} Rating", tab_result.get("rating", "Not Rated"))
    coverage_col.metric("Metric Coverage", format_coverage(tab_result.get("coverage", {})))
    if score is None:
        breakdown = tab_result.get("group_breakdown", {})
        reason = breakdown.get("reason")
        if reason == "minimum_weight_coverage":
            available = float(tab_result.get("coverage", {}).get("percentage", 0))
            required = float(breakdown.get("minimum_coverage", 0))
            st.warning(
                f"{name} is not rated: usable metric coverage is {available:.1f}%; "
                f"the model requires at least {required:.1f}%. See missing metrics below."
            )
        elif reason == "required_group_missing":
            group = str(breakdown.get("failed_group", "required component")).replace("_", " ")
            st.warning(f"{name} is not rated: too few usable metrics for {group}. See missing metrics below.")
        else:
            st.warning(f"{name} is not rated: insufficient usable data. See missing metrics below.")
    metrics = tab_result.get("metrics", [])
    render_metrics_table(metrics)

    if name == "Value":
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


def _overlay_trends_figure(
    history: pd.DataFrame, forward: pd.DataFrame, sources: pd.DataFrame,
    *, title: str, yaxis_title: str, actual_source: str, quarterly: bool = True,
) -> go.Figure:
    figure = go.Figure()
    colors = {"Revenue": "#72b7f2", "Net Income": "#0085ff", "Operating Cash Flow": "#ffa3a3", "P/S": "#a78bfa"}
    for metric in history.columns:
        color = colors.get(metric)
        figure.add_trace(go.Scatter(
            x=history.index, y=history[metric], name=metric, legendgroup=metric,
            mode="lines+markers" if quarterly else "lines", line={"color": color, "dash": "solid"},
            customdata=[actual_source] * len(history),
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<br>%{customdata}<extra>%{fullData.name}</extra>",
        ))
        if metric not in forward or forward[metric].dropna().empty:
            continue
        details = [
            [(date + pd.DateOffset(years=2)).strftime("%Y-%m-%d"),
             sources.loc[date, metric] if metric in sources and date in sources.index else "Model estimate"]
            for date in forward.index
        ]
        figure.add_trace(go.Scatter(
            x=forward.index, y=forward[metric], name=f"{metric} (+2Y estimate)", legendgroup=metric,
            mode="lines+markers" if quarterly else "lines", line={"color": color, "dash": "dash"},
            customdata=details,
            hovertemplate="Observation: %{x|%Y-%m-%d}<br>%{y:,.2f}<br>Forecast for: %{customdata[0]}<br>%{customdata[1]}<extra>%{fullData.name}</extra>",
        ))
    figure.update_layout(
        title=title, xaxis_title="Observation quarter" if quarterly else "Date", yaxis_title=yaxis_title,
        legend_title_text="Solid: reported · Dashed: +2Y estimate", hovermode="x unified",
        legend={"orientation": "h", "y": -0.2},
    )
    if quarterly:
        figure.update_xaxes(tickvals=history.index, ticktext=[f"Q{date.quarter} {date.year}" for date in history.index])
    else:
        figure.update_xaxes(type="date", tickformat="%b %Y")
    return figure


def financial_trends_figure(charts: dict) -> go.Figure:
    history = charts["financials"].sort_index()
    currency = history.attrs.get("financial_currency", "")
    return _overlay_trends_figure(
        history, charts.get("financial_estimates", pd.DataFrame()), charts.get("financial_estimate_sources", pd.DataFrame()),
        title="Financial Trends", yaxis_title=f"Quarterly value ({currency or 'reporting currency'})",
        actual_source="Reported quarterly financials",
    )


def render_financial_trends(charts: dict) -> None:
    history = charts.get("financials")
    if history is not None and not history.empty and history.attrs.get("basis") != "Quarterly":
        st.info("Financial Trends: click Analyze to replace the saved annual chart with quarterly data.")
        return
    if history is None or history.empty or history.dropna(how="all").empty:
        st.warning("Financial Trends: quarterly statements are unavailable.")
        return
    st.plotly_chart(financial_trends_figure(charts), width="stretch")
    st.caption(
        "Solid: reported quarterly values. Dashed at the same date: model estimate for that quarter +2 years "
        "(e.g. Q1 2023 → Q1 2025). Revenue uses same-quarter historical growth; net income and cash flow use "
        "published historical margins. Historical projections use earlier published data, not archived analyst consensus. "
        "Source statements may have been restated. Hover to see the forecast quarter and source."
    )
    if len(history) < 12:
        st.caption(f"Available history: {len(history)} quarterly points; the provider does not supply the full three years.")
    if charts["financial_estimates"].isna().any().any():
        st.caption("Forecast gaps mean that insufficient earlier data was available to calculate that point.")


def ps_trends_figure(charts: dict) -> go.Figure:
    ratios = charts["ps_ratios"]
    return _overlay_trends_figure(
        ratios[["P/S TTM"]].rename(columns={"P/S TTM": "P/S"}),
        ratios[["P/S +2Y"]].rename(columns={"P/S +2Y": "P/S"}),
        charts["ps_sources"][["P/S +2Y"]].rename(columns={"P/S +2Y": "P/S"}),
        title="P/S Ratio — Current and +2Y Estimate", yaxis_title="Price / Sales (×)",
        actual_source="Market capitalization / published TTM revenue (annual fallback if unavailable)",
        quarterly=False,
    )


def render_ps_trends(charts: dict) -> None:
    ratios = charts.get("ps_ratios")
    if ratios is None:
        st.info("P/S Ratio: click Analyze to load the current and +2Y series.")
        return
    if ratios.dropna(how="all").empty:
        st.warning("P/S Ratio: revenue or compatible capitalization data is unavailable.")
        return
    latest = ratios.iloc[-1]
    columns = st.columns(2)
    for column, metric in zip(columns, ("P/S TTM", "P/S +2Y"), strict=True):
        value = latest[metric]
        column.metric(metric, "Unavailable" if pd.isna(value) else f"{value:.2f}×")
    st.plotly_chart(ps_trends_figure(charts), width="stretch")
    st.caption(
        "Daily trading prices: both lines share the observation date and that date's market capitalization. "
        "Revenue updates when financial reports become available. Solid: trailing revenue. "
        "Dashed: revenue projected +2 years; this assumes unchanged capitalization, not a future share price. "
        "Historical forecasts are model projections using published annual/TTM growth. Only the current point can "
        "use today's analyst consensus for a matching two-year target. Missing shares, FX or revenue leave gaps."
    )


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
