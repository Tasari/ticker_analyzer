from __future__ import annotations

import html
import json

import pandas as pd
import plotly.express as px
import streamlit as st

from stock_analyzer import analyze_ticker, format_metric_value, load_config, save_config


st.set_page_config(page_title="Stock Analyzer", page_icon="chart_with_upwards_trend", layout="wide")


def main() -> None:
    st.title("Stock Analyzer")
    st.caption("Rule-based stock analysis using available yfinance data. This is not financial advice.")

    config = get_config()

    with st.sidebar:
        st.header("Analysis")
        ticker = st.text_input("Ticker", value="AAPL", help="Any ticker supported by yfinance.")
        range_options = ["1Y", "3Y", "5Y"]
        growth_range = st.selectbox("Growth range", range_options, index=1)
        fundamentals_range = st.selectbox("Fundamentals range", range_options, index=1)
        value_range = st.selectbox("Value range", range_options, index=1)
        analyze_clicked = st.button("Analyze", type="primary", use_container_width=True)
        st.divider()
        render_config_editor(config)

    if analyze_clicked or "last_result" not in st.session_state:
        with st.spinner("Fetching market and financial data..."):
            try:
                ranges = {
                    "Growth": growth_range,
                    "Fundamentals": fundamentals_range,
                    "Value": value_range,
                }
                st.session_state.last_result = analyze_ticker(ticker, ranges, config)
                st.session_state.last_error = None
            except Exception as exc:
                st.session_state.last_error = str(exc)
                st.session_state.last_result = None

    if st.session_state.get("last_error"):
        st.error(st.session_state.last_error)
        return

    result = st.session_state.get("last_result")
    if not result:
        return

    render_summary(result)
    render_tabs(result)


def get_config() -> dict:
    return load_config()


def render_summary(result: dict) -> None:
    score = result.get("overall_score")
    score_label = "Not Rated" if score is None else f"{score:.1f}/100"
    current_price = result.get("current_price")
    currency = result.get("currency", "")
    price_label = "Missing" if current_price is None else f"{current_price:,.2f} {currency}".strip()

    st.subheader(f"{result['company_name']} ({result['ticker']})")
    cols = st.columns(4)
    cols[0].metric("Overall Score", score_label)
    cols[1].metric("Rating", result.get("rating", "Not Rated"))
    cols[2].metric("Current Price", price_label)
    cols[3].metric("Available Tabs", sum(1 for tab in result["tabs"].values() if tab["score"] is not None))

    rating_cols = st.columns(3)
    for index, tab_name in enumerate(["Growth", "Fundamentals", "Value"]):
        tab_result = result["tabs"].get(tab_name, {})
        tab_score = tab_result.get("score")
        score_text = "" if tab_score is None else f"{tab_score:.1f}/100"
        tab_range = result.get("ranges", {}).get(tab_name, "")
        label = f"{tab_name} Rating" if not tab_range else f"{tab_name} Rating ({tab_range})"
        rating_cols[index].metric(label, tab_result.get("rating", "Not Rated"), score_text)

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
    score_col, rating_col = st.columns(2)
    score_col.metric(f"{name} Score", score_text)
    rating_col.metric(f"{name} Rating", tab_result.get("rating", "Not Rated"))
    metrics = tab_result.get("metrics", [])
    render_metrics_table(metrics)

    if name == "Growth":
        render_line_chart(charts.get("financials"), "Annual Financial Trends")
        render_line_chart(charts.get("prices"), "Selected Price Range")
    elif name == "Fundamentals":
        render_line_chart(charts.get("fundamentals"), "Debt and Assets")
    elif name == "Value":
        st.info("Value metrics compare current multiples and upside signals against approximate historical medians when enough data is available.")


def render_line_chart(frame: pd.DataFrame | None, title: str) -> None:
    if frame is None or frame.empty:
        st.warning(f"{title}: not enough data to chart.")
        return
    chart_frame = frame.copy()
    chart_frame.index = chart_frame.index.astype(str)
    chart_frame = chart_frame.reset_index(names="Date")
    melted = chart_frame.melt(id_vars="Date", var_name="Metric", value_name="Value")
    fig = px.line(melted, x="Date", y="Value", color="Metric", markers=True, title=title)
    st.plotly_chart(fig, use_container_width=True)


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
        st.write("Edit the JSON configuration, then save and analyze again.")
        edited = st.text_area(
            "metrics_config.json",
            value=json.dumps(config, indent=2),
            height=420,
            label_visibility="collapsed",
        )
        cols = st.columns(2)
        if cols[0].button("Save settings", use_container_width=True):
            try:
                parsed = json.loads(edited)
                save_config(parsed)
                st.success("Settings saved.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not save settings: {exc}")
        if cols[1].button("Reload settings", use_container_width=True):
            st.rerun()


if __name__ == "__main__":
    main()
