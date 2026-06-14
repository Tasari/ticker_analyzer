from __future__ import annotations

import html
import json

import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf
from streamlit_searchbox import st_searchbox

from ticker_analyzer import analyze_ticker, format_metric_value, load_config, save_config


st.set_page_config(page_title="Stock Analyzer", page_icon="chart_with_upwards_trend", layout="wide")


def main() -> None:
    st.title("Stock Analyzer")
    st.caption("Rule-based stock analysis using available yfinance data. This is not financial advice.")

    config = get_config()
    initialize_state()

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
            use_container_width=True,
            disabled=not st.session_state.selected_tickers,
        )
        st.divider()
        render_config_editor(config)

    if analyze_clicked or not st.session_state.analysis_results:
        with st.spinner("Fetching market and financial data..."):
            ranges = {
                "Growth": growth_range,
                "Fundamentals": fundamentals_range,
                "Value": value_range,
            }
            st.session_state.analysis_results, st.session_state.analysis_errors = analyze_selected_tickers(
                st.session_state.selected_tickers,
                ranges,
                config,
            )
            available_tickers = list(st.session_state.analysis_results)
            if available_tickers and st.session_state.active_ticker not in available_tickers:
                st.session_state.active_ticker = available_tickers[0]

    render_analysis_errors(st.session_state.analysis_errors)
    results = st.session_state.analysis_results
    if not results:
        if st.session_state.selected_tickers:
            st.error("No selected ticker could be analyzed.")
        else:
            st.info("Add a ticker to start the analysis.")
        return

    if len(results) == 1:
        render_company_analysis(next(iter(results.values())))
        return

    render_multi_ticker_analysis(results)


def get_config() -> dict:
    return load_config()


def initialize_state() -> None:
    if "selected_tickers" not in st.session_state:
        st.session_state.selected_tickers = ["AFRM"]
    if "analysis_results" not in st.session_state:
        st.session_state.analysis_results = {}
    if "analysis_errors" not in st.session_state:
        st.session_state.analysis_errors = {}
    if "active_ticker" not in st.session_state:
        st.session_state.active_ticker = "AFRM"


def render_ticker_search() -> None:
    selected = st_searchbox(
        search_tickers,
        key="ticker_search",
        label="Add ticker",
        placeholder="Type ticker or company name",
        edit_after_submit="disabled",
        clear_on_submit=True,
        debounce=250,
        help="Search by ticker or company name, then select up to five stocks.",
    )
    if not selected:
        return
    ticker = selected.split(" | ", maxsplit=1)[0].strip().upper()
    if ticker in st.session_state.selected_tickers:
        return
    if len(st.session_state.selected_tickers) >= 5:
        st.warning("You can compare up to five stocks.")
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


def analyze_selected_tickers(tickers: list[str], ranges: dict[str, str], config: dict) -> tuple[dict, dict]:
    results = {}
    errors = {}
    for ticker in tickers:
        try:
            results[ticker] = analyze_ticker(ticker, ranges, config)
        except Exception as exc:
            errors[ticker] = str(exc)
    return results, errors


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
    render_company_cards(ranked_results)
    render_comparison_table(ranked_results)
    with st.expander("Compare all metrics", expanded=False):
        render_metric_comparison(ranked_results)


def rank_results(results: dict[str, dict], sort_option: str) -> list[dict]:
    ranked = list(results.values())
    if sort_option == "Overall":
        score_for = lambda result: result.get("overall_score")
    else:
        score_for = lambda result: result.get("tabs", {}).get(sort_option, {}).get("score")
    return sorted(
        ranked,
        key=lambda result: -1 if score_for(result) is None else score_for(result),
        reverse=True,
    )


def render_ranking(results: list[dict], sort_option: str) -> None:
    st.markdown(f"#### {sort_option} Ranking")
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
                "Growth": format_tab_summary(result, "Growth"),
                "Fundamentals": format_tab_summary(result, "Fundamentals"),
                "Value": format_tab_summary(result, "Value"),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def format_company_price(result: dict) -> str:
    price = result.get("current_price")
    currency = result.get("currency", "")
    return "Missing" if price is None else f"{price:,.2f} {currency}".strip()


def format_score(score: float | None) -> str:
    return "Missing" if score is None else f"{score:.1f}/100"


def format_tab_summary(result: dict, tab_name: str) -> str:
    tab_result = result.get("tabs", {}).get(tab_name, {})
    return f"{format_score(tab_result.get('score'))} | {tab_result.get('rating', 'Not Rated')}"


@st.cache_data(ttl=900, show_spinner=False)
def search_tickers(searchterm: str) -> list[str]:
    query = searchterm.strip()
    if len(query) < 1:
        return []
    try:
        results = yf.Search(
            query,
            max_results=10,
            news_count=0,
            lists_count=0,
            include_cb=False,
            include_nav_links=False,
            include_research=False,
            include_cultural_assets=False,
            enable_fuzzy_query=True,
            recommended=0,
        ).quotes
    except Exception:
        return []

    suggestions = []
    for result in results:
        if result.get("quoteType") != "EQUITY":
            continue
        symbol = result.get("symbol")
        if not symbol:
            continue
        name = result.get("longname") or result.get("shortname") or symbol
        exchange = result.get("exchDisp") or result.get("exchange") or ""
        suggestions.append(f"{symbol} | {name} | {exchange}".rstrip(" |"))
    return suggestions


def render_summary(result: dict) -> None:
    score = result.get("overall_score")
    score_label = "Not Rated" if score is None else f"{score:.1f}/100"
    current_price = result.get("current_price")
    currency = result.get("currency", "")
    price_label = "Missing" if current_price is None else f"{current_price:,.2f} {currency}".strip()

    st.subheader(f"{result['company_name']} ({result['ticker']})")
    cols = st.columns(5)
    cols[0].metric("Overall Score", score_label)
    cols[1].metric("Rating", result.get("rating", "Not Rated"))
    cols[2].metric("Current Price", price_label)
    cols[3].metric("Analysis Profile", result.get("profile", "Industrial"))
    cols[4].metric("Available Tabs", sum(1 for tab in result["tabs"].values() if tab["score"] is not None))

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
