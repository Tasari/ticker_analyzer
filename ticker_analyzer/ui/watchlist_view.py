from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st
from streamlit_searchbox import st_searchbox

from ticker_analyzer.config import load_config
from ticker_analyzer.ticker_symbols import MARKET_SUFFIXES, ticker_for_market
from ticker_analyzer.ui.analysis_actions import analyze_selected_tickers, search_tickers
from ticker_analyzer.watchlist import (
    ALERT_LIMIT,
    add_watch_ticker,
    evaluate_watchlist,
    normalize_alerts,
    normalize_snapshots,
    normalize_watchlist,
)


def render_watchlist() -> None:
    st.subheader("Watchlist and Alerts")
    st.caption(
        "Watchlist tickers are independent of Stock Analyzer. Alerts are checked when you refresh this page; "
        "the app does not run in the background while it is closed."
    )
    _render_add_controls()
    items = normalize_watchlist(st.session_state.get("watchlist"))
    st.session_state["watchlist"] = items
    if not items:
        st.info("Add a ticker to start monitoring ratings, price, score and data availability.")
        _render_alert_history()
        return

    items = _render_watchlist_editor(items)
    if not items:
        _render_alert_history()
        return
    ranges = _render_range_controls()
    if not st.session_state.get("watchlist_auto_refresh_attempted"):
        st.session_state["watchlist_auto_refresh_attempted"] = True
        with st.spinner(f"Checking {len(items)} watchlist ticker(s)..."):
            _refresh_tickers([item["ticker"] for item in items], ranges)
    failed = _failed_tickers(items)
    actions = st.columns([1, 1, 4])
    refresh_all = actions[0].button("Refresh all", type="primary", width="stretch")
    retry_failed = actions[1].button(
        f"Check again ({len(failed)})",
        width="stretch",
        disabled=not failed,
    )
    if refresh_all or retry_failed:
        targets = [item["ticker"] for item in items] if refresh_all else failed
        with st.spinner(f"Refreshing {len(targets)} watchlist ticker(s)..."):
            _refresh_tickers(targets, ranges)

    _render_current_status(items)
    _render_retry_queue(items)
    _render_alert_history()


def _render_add_controls() -> None:
    selected = st_searchbox(
        search_tickers,
        key="watchlist_search",
        label="Add watched ticker",
        placeholder="Type ticker or company name",
        edit_after_submit="disabled",
        clear_on_submit=True,
        debounce=250,
    )
    if selected:
        ticker = selected.split(" | ", maxsplit=1)[0]
        _add_and_rerun(ticker)

    with st.expander("Add exact ticker from another market", expanded=False):
        columns = st.columns([2, 2, 1])
        market = columns[0].selectbox(
            "Market",
            list(MARKET_SUFFIXES),
            key="watchlist_manual_market",
        )
        symbol = columns[1].text_input("Ticker symbol", key="watchlist_manual_symbol")
        ticker = ticker_for_market(symbol, market)
        if columns[2].button(
            "Add",
            key="watchlist_add_exact",
            disabled=ticker is None,
            width="stretch",
        ):
            _add_and_rerun(ticker)


def _add_and_rerun(ticker: Any) -> None:
    items, added = add_watch_ticker(st.session_state.get("watchlist"), ticker)
    st.session_state["watchlist"] = items
    if added:
        st.session_state["watchlist_auto_refresh_attempted"] = False
        st.rerun()
    st.info("Ticker is already watched or cannot be added.")


def _render_watchlist_editor(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    st.markdown("#### Monitored tickers and thresholds")
    frame = pd.DataFrame(
        [
            {
                "Remove": False,
                "Ticker": item["ticker"],
                "Price at/above": item.get("price_above"),
                "Price at/below": item.get("price_below"),
                "Score at/above": item.get("score_above"),
                "Score at/below": item.get("score_below"),
            }
            for item in items
        ]
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        key="watchlist_editor",
        disabled=["Ticker"],
        column_config={
            "Remove": st.column_config.CheckboxColumn(),
            "Price at/above": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Price at/below": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Score at/above": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format="%.2f"),
            "Score at/below": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, format="%.2f"),
        },
    )
    updated = normalize_watchlist(
        [
            {
                "ticker": row["Ticker"],
                "price_above": row["Price at/above"],
                "price_below": row["Price at/below"],
                "score_above": row["Score at/above"],
                "score_below": row["Score at/below"],
            }
            for row in edited.to_dict("records")
            if not row["Remove"]
        ]
    )
    if updated != items:
        st.session_state["watchlist"] = updated
        st.session_state["watchlist_snapshots"] = normalize_snapshots(
            st.session_state.get("watchlist_snapshots"),
            updated,
        )
    return updated


def _render_range_controls() -> dict[str, str]:
    with st.expander("Watchlist analysis ranges", expanded=False):
        columns = st.columns(3)
        options = ["1Y", "2Y", "3Y"]
        growth = columns[0].selectbox("Growth range", options, key="watchlist_growth_range")
        fundamentals = columns[1].selectbox(
            "Fundamentals range",
            options,
            key="watchlist_fundamentals_range",
        )
        value = columns[2].selectbox("Value range", options, key="watchlist_value_range")
    return {"Growth": growth, "Fundamentals": fundamentals, "Value": value}


def _refresh_tickers(tickers: list[str], ranges: dict[str, str]) -> None:
    items = normalize_watchlist(st.session_state.get("watchlist"))
    selected_items = [item for item in items if item["ticker"] in set(tickers)]
    if not selected_items:
        return
    results, errors = analyze_selected_tickers(
        [item["ticker"] for item in selected_items],
        ranges,
        load_config(),
        cache_token=time.time_ns(),
    )
    refresh = evaluate_watchlist(
        selected_items,
        st.session_state.get("watchlist_snapshots"),
        results,
        errors,
    )
    merged_snapshots = dict(st.session_state.get("watchlist_snapshots") or {})
    merged_snapshots.update(refresh.snapshots)
    st.session_state["watchlist_snapshots"] = normalize_snapshots(merged_snapshots, items)
    existing_alerts = normalize_alerts(st.session_state.get("watchlist_alerts"))
    st.session_state["watchlist_alerts"] = normalize_alerts(
        [*reversed(refresh.alerts), *existing_alerts][:ALERT_LIMIT]
    )


def _failed_tickers(items: list[dict[str, Any]]) -> list[str]:
    snapshots = normalize_snapshots(st.session_state.get("watchlist_snapshots"), items)
    return [item["ticker"] for item in items if snapshots.get(item["ticker"], {}).get("status") == "error"]


def _render_current_status(items: list[dict[str, Any]]) -> None:
    snapshots = normalize_snapshots(st.session_state.get("watchlist_snapshots"), items)
    rows = []
    for item in items:
        snapshot = snapshots.get(item["ticker"], {})
        rows.append(
            {
                "Ticker": item["ticker"],
                "Company": snapshot.get("company_name"),
                "Price": snapshot.get("price"),
                "Score": snapshot.get("score"),
                "Rating": snapshot.get("rating"),
                "Missing data": len(snapshot.get("missing", [])),
                "Missing details": "; ".join(snapshot.get("missing", [])) or None,
                "Status": snapshot.get("status", "Not checked"),
                "Last checked": _display_timestamp(snapshot.get("checked_at")),
            }
        )
    st.markdown("#### Latest check")
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Price": st.column_config.NumberColumn(format="%.4f"),
            "Score": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def _render_retry_queue(items: list[dict[str, Any]]) -> None:
    snapshots = normalize_snapshots(st.session_state.get("watchlist_snapshots"), items)
    rows = [
        {
            "Ticker": ticker,
            "Last error": snapshot.get("error"),
            "Last attempt": _display_timestamp(snapshot.get("checked_at")),
        }
        for ticker, snapshot in snapshots.items()
        if snapshot.get("status") == "error"
    ]
    if not rows:
        return
    st.markdown("#### Check again")
    st.warning("These tickers failed during the latest refresh and kept their last successful values.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_alert_history() -> None:
    alerts = normalize_alerts(st.session_state.get("watchlist_alerts"))
    st.markdown("#### Alerts")
    if not alerts:
        st.caption("No alerts yet.")
        return
    unread = [alert for alert in alerts if not alert["read"]]
    for alert in unread[:10]:
        st.warning(f"{alert['ticker']}: {alert['message']}")
    actions = st.columns([1, 1, 4])
    if actions[0].button("Mark all read", disabled=not unread, width="stretch"):
        st.session_state["watchlist_alerts"] = [{**alert, "read": True} for alert in alerts]
        st.rerun()
    if actions[1].button("Clear alerts", width="stretch"):
        st.session_state["watchlist_alerts"] = []
        st.rerun()
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "New": not alert["read"],
                    "Ticker": alert["ticker"],
                    "Type": alert["kind"].replace("_", " ").title(),
                    "Message": alert["message"],
                    "Created": _display_timestamp(alert["created_at"]),
                }
                for alert in alerts
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _display_timestamp(value: Any) -> str | None:
    text = str(value or "")
    return text[:16].replace("T", " ") if text else None
