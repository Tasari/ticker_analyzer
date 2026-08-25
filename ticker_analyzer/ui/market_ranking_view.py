from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ticker_analyzer.ranking_storage import load_ranking
from ticker_analyzer.ui.config_view import mutation_allowed

ETF_RANKING_PATH = Path("data/etf_ranking_v1.json")
CRYPTO_RANKING_PATH = Path("data/crypto_ranking_v1.json")


def _refresh_etf_ranking() -> dict[str, Any]:
    from ticker_analyzer.asset_rankings import refresh_etf_ranking

    return refresh_etf_ranking()


def _refresh_crypto_ranking() -> dict[str, Any]:
    from ticker_analyzer.asset_rankings import refresh_crypto_ranking

    return refresh_crypto_ranking()


def render_etf_ranking() -> None:
    _render_market_ranking(
        title="ETF Ranking",
        description=(
            "Exchange-traded funds from supported European venues, ranked across momentum, "
            "volatility and trading liquidity. These are not scored as companies."
        ),
        path=ETF_RANKING_PATH,
        refresh=_refresh_etf_ranking,
        key="etf",
        columns={
            "rank": "Rank", "ticker": "Ticker", "name": "Fund", "market": "Market",
            "exchange": "Exchange", "currency": "Currency", "price": "Price",
            "overall_score": "Market Score", "rating": "Signal", "data_coverage": "Coverage",
            "return_1m": "1M", "return_3m": "3M", "return_6m": "6M", "return_1y": "1Y",
            "volatility_1m": "Volatility 1M", "traded_value": "Traded Value",
        },
    )


def render_crypto_ranking() -> None:
    _render_market_ranking(
        title="Crypto Ranking",
        description=(
            "Top 100 cryptocurrencies by market capitalization, ranked across momentum, "
            "market size, liquidity and distance from the all-time high."
        ),
        path=CRYPTO_RANKING_PATH,
        refresh=_refresh_crypto_ranking,
        key="crypto",
        columns={
            "rank": "Rank", "ticker": "Ticker", "name": "Asset", "price": "Price (USD)",
            "market_cap": "Market Cap", "overall_score": "Market Score", "rating": "Signal",
            "data_coverage": "Coverage", "return_24h": "24H", "return_7d": "7D",
            "return_30d": "30D", "return_200d": "200D", "return_1y": "1Y",
            "ath_drawdown": "From ATH", "total_volume": "Volume",
        },
    )


def _render_market_ranking(
    *, title: str, description: str, path: Path, refresh: Callable[[], dict[str, Any]],
    key: str, columns: dict[str, str],
) -> None:
    st.subheader(title)
    st.caption(description)
    refresh_allowed = mutation_allowed("ALLOW_RANKING_REFRESH")
    if st.button(
        f"Update {title}", type="primary", key=f"{key}_ranking_update", disabled=not refresh_allowed,
    ):
        try:
            with st.spinner(f"Updating {title.lower()}..."):
                refresh()
        except Exception as exc:
            st.error(f"{title} update failed: {type(exc).__name__}: {exc}")
        else:
            st.success(f"{title} updated.")
            st.rerun()
    if not refresh_allowed:
        st.caption("Ranking refresh is disabled in production mode.")

    payload = load_ranking(path)
    rows = payload.get("companies", [])
    metadata = payload.get("metadata", {})
    if not rows:
        st.info(f"No {title.lower()} snapshot yet. Use the update button to generate it.")
        return
    st.caption(
        f"Generated {metadata.get('generated_at', 'unknown')} · "
        f"scored {metadata.get('scored', 0)}/{metadata.get('analyzed', len(rows))}."
    )
    filter_cols = st.columns([3, 1, 1])
    query = filter_cols[0].text_input(
        "Search", placeholder="Ticker or name", key=f"{key}_ranking_search",
    ).strip().casefold()
    minimum_score = filter_cols[1].slider(
        "Minimum score", 0, 100, 0, key=f"{key}_ranking_minimum_score",
    )
    maximum_rows = filter_cols[2].selectbox(
        "Rows", [25, 50, 100, 250, 500, 1000], index=2, key=f"{key}_ranking_rows",
    )
    market_options = sorted({str(row["market"]) for row in rows if row.get("market")})
    selected_markets = st.multiselect(
        "Market", market_options, key=f"{key}_ranking_markets",
    ) if market_options else []
    filtered = [
        row for row in rows
        if row.get("overall_score") is not None
        and float(row["overall_score"]) >= minimum_score
        and (not selected_markets or row.get("market") in selected_markets)
        and (not query or query in str(row.get("ticker") or "").casefold()
             or query in str(row.get("name") or "").casefold())
    ][:maximum_rows]
    st.caption(f"Showing {len(filtered):,} of {len(rows):,} instruments in the snapshot.")
    frame = pd.DataFrame(filtered)
    if frame.empty:
        st.info("No instruments match the selected filters.")
        return
    available = [field for field in columns if field in frame.columns]
    percent_columns = {
        label: st.column_config.NumberColumn(format="%.2f%%")
        for field, label in columns.items() if field.startswith("return_") or field == "ath_drawdown"
    }
    st.dataframe(
        frame[available].rename(columns=columns), hide_index=True, width="stretch",
        key=f"{key}_ranking_table",
        column_config={
            **percent_columns,
            "Market Score": st.column_config.NumberColumn(format="%.1f"),
            "Coverage": st.column_config.NumberColumn(format="%.1f%%"),
            "Market Cap": st.column_config.NumberColumn(format="$%.0f"),
            "Volume": st.column_config.NumberColumn(format="$%.0f"),
        },
    )
    errors = payload.get("errors", [])
    if errors:
        with st.expander(f"Provider errors ({len(errors)})", expanded=False):
            st.dataframe(pd.DataFrame(errors), hide_index=True, width="stretch")
    st.caption(
        "The market score is a relative screen, not a company fundamental rating or investment advice. "
        "Verify instrument availability and costs with your broker."
    )
