from __future__ import annotations

from hashlib import sha256

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ticker_analyzer.account_statement import (
    AccountStatementError,
    PositionContribution,
    StatementAnalysis,
    StatementOverview,
    StatementRangeAnalysis,
    analyze_account_statement,
    analyze_position_contributions,
    analyze_statement_range,
    inspect_account_statement,
    list_statement_assets,
    read_statement_sheet,
)
from ticker_analyzer.portfolio_performance import (
    BenchmarkError,
    calculate_drawdown,
    fetch_benchmark_growth,
    monthly_performance,
    parse_comparison_symbols,
)
from ticker_analyzer.returns_table import (
    ACCOUNT_RETURNS_NAME_STATE_KEY,
    ACCOUNT_RETURNS_PAYLOAD_STATE_KEY,
    ACCOUNT_RETURNS_STATE_KEY,
    ACCOUNT_STATEMENT_NAME_STATE_KEY,
    ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY,
    ACCOUNT_STATEMENT_TICKER,
    GrowthPoint,
    ReturnsRangeAnalysis,
    ReturnsTable,
    ReturnsTableError,
    analyze_returns_range,
    parse_returns_table,
)


def render_account_statement() -> None:
    st.subheader("Account Statement")
    st.caption(
        "Upload an eToro XLSX account statement and, optionally, its monthly returns CSV. "
        "Files are processed in memory for this session and are not added to the repository."
    )
    uploaded = st.file_uploader(
        "Account statement",
        type=["xlsx"],
        accept_multiple_files=False,
        help="Current importer supports eToro account statements up to 10 MB.",
        key="account_statement_upload",
    )
    returns_upload = st.file_uploader(
        "Returns table (optional)",
        type=["csv"],
        accept_multiple_files=False,
        help="Optional eToro monthly returns CSV with Year and Jan through Dec columns.",
        key="account_returns_upload",
    )
    st.button(
        "Clear imported files",
        help="Remove the statement, returns table, and ACC_STMT from this session.",
        key="clear_account_statement_imports",
        on_click=_clear_imported_statement_state,
    )

    if uploaded is not None:
        st.session_state[ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY] = uploaded.getvalue()
        st.session_state[ACCOUNT_STATEMENT_NAME_STATE_KEY] = uploaded.name
    if returns_upload is not None:
        st.session_state[ACCOUNT_RETURNS_PAYLOAD_STATE_KEY] = returns_upload.getvalue()
        st.session_state[ACCOUNT_RETURNS_NAME_STATE_KEY] = returns_upload.name

    payload = st.session_state.get(ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY)
    if not isinstance(payload, bytes):
        st.info("Choose an eToro account statement in XLSX format to begin.")
        return

    try:
        overview = inspect_account_statement(payload)
    except AccountStatementError as exc:
        st.error(str(exc))
        return

    returns_table: ReturnsTable | None = None
    returns_payload = st.session_state.get(ACCOUNT_RETURNS_PAYLOAD_STATE_KEY)
    if isinstance(returns_payload, bytes):
        try:
            returns_table = parse_returns_table(returns_payload)
        except ReturnsTableError as exc:
            st.session_state.pop(ACCOUNT_RETURNS_STATE_KEY, None)
            st.warning(f"Returns table could not be used: {exc} Falling back to statement estimates.")
        else:
            st.session_state[ACCOUNT_RETURNS_STATE_KEY] = returns_table
            returns_name = st.session_state.get(ACCOUNT_RETURNS_NAME_STATE_KEY, "returns table")
            st.success(
                f"Loaded {returns_name}: "
                f"{returns_table.first_month:%Y-%m} through {returns_table.last_month:%Y-%m}"
            )
            st.caption(f"{ACCOUNT_STATEMENT_TICKER} is now available in Portfolio Simulation.")
    else:
        st.session_state.pop(ACCOUNT_RETURNS_STATE_KEY, None)

    statement_name = st.session_state.get(ACCOUNT_STATEMENT_NAME_STATE_KEY, "account statement")
    st.success(f"Loaded {statement_name}")
    analysis_tab, preview_tab = st.tabs(["Analysis", "Data preview"])
    with analysis_tab:
        _render_analysis(payload, returns_table)
    with preview_tab:
        _render_data_preview(payload, overview)


def _clear_imported_statement_state() -> None:
    for key in (
        ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY,
        ACCOUNT_STATEMENT_NAME_STATE_KEY,
        ACCOUNT_RETURNS_PAYLOAD_STATE_KEY,
        ACCOUNT_RETURNS_NAME_STATE_KEY,
        ACCOUNT_RETURNS_STATE_KEY,
        "account_statement_upload",
        "account_returns_upload",
    ):
        st.session_state.pop(key, None)
    selected = st.session_state.get("selected_tickers", [])
    st.session_state["selected_tickers"] = [
        ticker for ticker in selected if ticker != ACCOUNT_STATEMENT_TICKER
    ]
    if st.session_state.get("active_ticker") == ACCOUNT_STATEMENT_TICKER:
        st.session_state["active_ticker"] = next(
            iter(st.session_state.get("analysis_results", {})),
            None,
        )
    st.session_state.pop("simulation_output", None)


def _render_analysis(payload: bytes, returns_table: ReturnsTable | None = None) -> None:
    try:
        analysis = analyze_account_statement(payload)
    except AccountStatementError as exc:
        st.error(str(exc))
        return

    statement_start = analysis.start_date.date()
    statement_end = analysis.end_date.date()
    period_key = f"{statement_start.isoformat()}_{statement_end.isoformat()}"
    date_columns = st.columns(2)
    selected_start = date_columns[0].date_input(
        "Start date",
        value=statement_start,
        min_value=statement_start,
        max_value=statement_end,
        help="Inclusive first day of the analysis period.",
        key=f"account_statement_analysis_start_{period_key}",
    )
    selected_end = date_columns[1].date_input(
        "End date",
        value=statement_end,
        min_value=statement_start,
        max_value=statement_end,
        help="Inclusive last day of the analysis period.",
        key=f"account_statement_analysis_end_{period_key}",
    )
    if selected_end < selected_start:
        st.error("End date must not be before start date.")
        return
    available_assets = list_statement_assets(payload)
    statement_key = sha256(payload).hexdigest()[:12]
    excluded_assets = tuple(
        st.multiselect(
            "Exclude instruments",
            options=available_assets,
            help=(
                "Removes Position-ID-linked activity and holdings for selected instruments. "
                "For example, select GOLD to estimate the portfolio without Gold transactions."
            ),
            key=f"account_statement_excluded_assets_{period_key}_{statement_key}",
        )
    )
    if excluded_assets:
        st.info(
            "Filtered mode: selected instruments are removed from linked realized transactions, fees, dividends, "
            "holdings exposure, and contribution tables. Historical unrealized values are aggregated by eToro, "
            "so filtered total returns remain estimates."
        )
        analysis = analyze_account_statement(payload, excluded_assets=excluded_assets)
    try:
        range_analysis = analyze_statement_range(
            payload,
            selected_start,
            selected_end,
            excluded_assets=excluded_assets,
        )
    except AccountStatementError as exc:
        st.error(str(exc))
        return
    returns_analysis: ReturnsRangeAnalysis | None = None
    if returns_table is not None and not excluded_assets:
        try:
            returns_analysis = analyze_returns_range(
                returns_table,
                selected_start,
                selected_end,
            )
        except ReturnsTableError as exc:
            st.warning(f"Returns table does not cover this range: {exc} Using statement estimates.")
    elif returns_table is not None:
        st.caption("The returns table covers the complete portfolio, so filtered mode uses statement estimates.")
    full_period = (
        selected_start == statement_start
        and selected_end == statement_end
    )

    st.markdown("#### Selected-period performance")
    if full_period and not excluded_assets:
        _render_full_period_performance(analysis, returns_analysis)
    else:
        _render_partial_period_performance(
            range_analysis,
            analysis.currency,
            returns_analysis,
        )

    st.markdown("#### Growth of 10,000")
    portfolio_growth: tuple[GrowthPoint, ...] = ()
    if returns_analysis is not None:
        portfolio_growth = returns_analysis.growth
        detail = (
            "Partial boundary months are geometrically prorated from their monthly return."
            if returns_analysis.partial_months_estimated
            else "All selected months use their complete eToro monthly return."
        )
        st.caption(
            f"Based on {returns_analysis.covered_months} monthly return(s) from the imported "
            f"returns table. {detail}"
        )
    else:
        portfolio_growth = _statement_growth(range_analysis)
        if portfolio_growth:
            st.caption(
                "Based on the statement-derived estimated P/L path, normalized to its Modified "
                "Dietz return, because no usable returns table covers the selected range."
            )
    comparison_input = st.text_input(
        "Comparison tickers",
        value="SPY, QQQ",
        help=(
            "Enter up to 10 Yahoo Finance tickers separated by commas or spaces. "
            "Your Account Statement portfolio is included automatically."
        ),
        key="account_statement_benchmarks",
    )
    comparison_growth: dict[str, tuple[GrowthPoint, ...]] = {}
    try:
        comparison_symbols = parse_comparison_symbols(comparison_input)
    except BenchmarkError as exc:
        st.warning(str(exc))
        comparison_symbols = ()
    for symbol in comparison_symbols:
        try:
            comparison_growth[symbol] = _cached_benchmark_growth(
                symbol,
                selected_start,
                selected_end,
            )
        except BenchmarkError as exc:
            st.warning(f"{symbol}: {exc}")
    if portfolio_growth:
        st.plotly_chart(
            _growth_chart(portfolio_growth, comparison_growth),
            width="stretch",
        )
        _render_performance_comparison(portfolio_growth, comparison_growth)
        _render_monthly_performance(portfolio_growth)

    contributions = analyze_position_contributions(
        payload,
        selected_start,
        selected_end,
        excluded_assets=excluded_assets,
    )
    _render_position_contributions(contributions)

    st.markdown("#### Portfolio snapshot")
    snapshot_date = analysis.holdings_snapshot_date or analysis.end_date.date()
    st.caption(
        f"Holdings snapshot as of {snapshot_date:%Y-%m-%d}; it does not move with the selected range "
        "and is not refreshed with live prices."
    )
    snapshot = st.columns(3)
    snapshot[0].metric("Portfolio value", _money(analysis.ending_unrealized_equity, analysis.currency))
    snapshot[1].metric("Included open positions", f"{analysis.open_positions:,}")
    snapshot[2].metric(
        "Gross exposure",
        _money(analysis.long_exposure + analysis.short_exposure, analysis.currency),
        help="Absolute exposure from Holdings; this is not the portfolio equity value.",
    )
    _render_exposure_and_cash_flows(analysis, selected_start, selected_end)


def _render_full_period_performance(
    analysis: StatementAnalysis,
    returns_analysis: ReturnsRangeAnalysis | None = None,
) -> None:
    currency = analysis.currency
    primary = st.columns(4)
    primary[0].metric("Total P/L", _money(analysis.total_profit_loss, currency))
    primary[1].metric(
        "ROI",
        _percent(analysis.simple_roi),
        help="Total P/L divided by beginning equity plus positive contributions.",
    )
    if returns_analysis is not None:
        primary[2].metric(
            "Returns-table CAGR",
            _percent(returns_analysis.annualized_return),
            help="The imported time-weighted return annualized over the selected period.",
        )
        primary[3].metric(
            "Returns-table TWR",
            _percent(returns_analysis.period_return),
            help="Geometrically compounded monthly eToro returns.",
        )
    else:
        primary[2].metric(
            "Annualized ROI",
            _percent(analysis.annualized_roi),
            help="ROI annualized over the exact statement duration (CAGR-style).",
        )
        primary[3].metric(
            "Estimated TWR",
            _percent(analysis.modified_dietz_return),
            help="Modified Dietz estimate using dated external cash flows; not a true daily-valued TWR.",
        )
    secondary = st.columns(4)
    secondary[0].metric("Net external flows", _money(analysis.net_external_flows, currency))
    secondary[1].metric("Closed P/L", _money(analysis.closed_positions_profit_loss, currency))
    secondary[2].metric("Dividends", _money(analysis.dividends, currency))
    secondary[3].metric("Fees", _money(analysis.fees, currency))
    for warning in analysis.warnings:
        st.warning(warning)
    st.plotly_chart(_profit_loss_waterfall(analysis), width="stretch")
    if returns_analysis is not None:
        st.caption(
            "The imported TWR geometrically compounds eToro monthly returns and supersedes "
            "the statement-only Modified Dietz estimate for this range."
        )
    else:
        st.caption(
            "Modified Dietz weights each external cash flow by how long it remained invested. "
            "A true TWR requires portfolio valuations at cash-flow boundaries."
        )


def _render_partial_period_performance(
    range_analysis: StatementRangeAnalysis,
    currency: str,
    returns_analysis: ReturnsRangeAnalysis | None = None,
) -> None:
    if returns_analysis is not None:
        st.info(
            "TWR and CAGR use the imported eToro monthly returns. P/L, ROI, and boundary equity "
            "still combine exact Account Activity with interpolated Holdings valuations."
        )
    else:
        st.info(
            "Estimated total-return metrics combine exact Account Activity with interpolated "
            "unrealized P/L from eToro Holdings snapshots. They are directional estimates, "
            "not exact historical portfolio valuations."
        )
    estimated = st.columns(4)
    estimated[0].metric(
        "Estimated total P/L",
        _money(range_analysis.estimated_total_profit_loss, currency),
    )
    estimated[1].metric("Estimated ROI", _percent(range_analysis.estimated_roi))
    if returns_analysis is not None:
        estimated[2].metric(
            "Returns-table CAGR",
            _percent(returns_analysis.annualized_return),
            help="The imported time-weighted return annualized over the selected period.",
        )
        estimated[3].metric(
            "Returns-table TWR",
            _percent(returns_analysis.period_return),
            help="Geometrically compounded monthly eToro returns.",
        )
    else:
        estimated[2].metric(
            "Estimated annualized ROI",
            _percent(range_analysis.estimated_annualized_roi),
            help="Estimated ROI annualized over the inclusive selected period (CAGR-style).",
        )
        estimated[3].metric(
            "Estimated TWR",
            _percent(range_analysis.estimated_modified_dietz_return),
            help="Modified Dietz estimate using exact dated external cash flows.",
        )
    st.caption(
        f"Estimated equity: {_money(range_analysis.estimated_beginning_equity, currency)} "
        f"→ {_money(range_analysis.estimated_ending_equity, currency)}. "
        f"Used {range_analysis.holdings_snapshot_count} intermediate Holdings snapshot(s); "
        "the nearest valuation anchor was at most "
        f"{range_analysis.max_boundary_anchor_distance_days} day(s) from a selected boundary."
    )
    for warning in range_analysis.valuation_warnings:
        st.warning(warning)

    st.markdown("##### Realized components")
    primary = st.columns(3)
    primary[0].metric("Realized P/L", _money(range_analysis.realized_profit_loss, currency))
    primary[1].metric("Closed P/L", _money(range_analysis.closed_positions_profit_loss, currency))
    primary[2].metric("Dividends", _money(range_analysis.dividends, currency))
    secondary = st.columns(3)
    secondary[0].metric("Fees", _money(range_analysis.fees, currency))
    secondary[1].metric(
        "Other / reconciliation",
        _money(range_analysis.other_performance, currency),
    )
    secondary[2].metric("Net external flows", _money(range_analysis.net_external_flows, currency))
    st.plotly_chart(_realized_performance_chart(range_analysis, currency), width="stretch")


def _render_exposure_and_cash_flows(analysis: StatementAnalysis, selected_start: object, selected_end: object) -> None:
    exposure_col, cash_flow_col = st.columns(2)
    with exposure_col:
        st.markdown("##### Exposure by asset type")
        if analysis.exposure_by_type:
            st.plotly_chart(_exposure_chart(analysis), width="stretch")
            st.caption(
                f"Long {_money(analysis.long_exposure, analysis.currency)} · "
                f"Short {_money(analysis.short_exposure, analysis.currency)}. "
                "Exposure may exceed equity because positions can be leveraged or copied."
            )
        else:
            st.info("No holdings exposure was available in this statement.")
    with cash_flow_col:
        st.markdown("##### External cash flows in selected period")
        flows = [
            flow
            for flow in analysis.cash_flows
            if selected_start <= flow.occurred_at.date() <= selected_end
        ]
        if flows:
            cash_flow_frame = pd.DataFrame(
                [
                    {
                        "Date": flow.occurred_at,
                        "Type": flow.kind,
                        f"Amount ({analysis.currency})": flow.amount,
                        "Date estimated": flow.estimated_date,
                    }
                    for flow in flows
                ]
            )
            st.dataframe(cash_flow_frame, width="stretch", hide_index=True)
        else:
            st.info("No external cash flows occurred during the selected period.")


def _render_data_preview(payload: bytes, overview: StatementOverview) -> None:
    period = _format_period(overview.start_date, overview.end_date)
    metric_columns = st.columns(3)
    metric_columns[0].metric("Currency", overview.currency or "Unknown")
    metric_columns[1].metric("Statement period", period)
    metric_columns[2].metric("Worksheets", len(overview.sheets))

    sheet_names = [sheet.name for sheet in overview.sheets]
    selected_sheet = st.selectbox("Worksheet preview", sheet_names)
    selected_info = next(sheet for sheet in overview.sheets if sheet.name == selected_sheet)
    st.caption(f"{selected_info.data_rows:,} data rows · {selected_info.columns:,} columns")
    try:
        preview = read_statement_sheet(payload, selected_sheet)
    except AccountStatementError as exc:
        st.error(str(exc))
        return

    frame = _arrow_safe_frame(preview.rows, preview.columns)
    st.dataframe(frame, width="stretch", hide_index=True)
    if preview.truncated:
        st.info(
            f"Showing the first {len(preview.rows):,} of {preview.total_rows:,} rows "
            "to keep memory usage bounded."
        )


def _profit_loss_waterfall(analysis: StatementAnalysis) -> go.Figure:
    labels = [
        "Beginning equity",
        "External flows",
        "Closed P/L",
        "Dividends",
        "Fees",
        "Other performance",
        "Unrealized P/L change",
        "Ending equity",
    ]
    values = [
        analysis.beginning_unrealized_equity,
        analysis.net_external_flows,
        analysis.closed_positions_profit_loss,
        analysis.dividends,
        analysis.fees,
        analysis.other_performance,
        analysis.unrealized_profit_loss_change,
        analysis.ending_unrealized_equity,
    ]
    figure = go.Figure(
        go.Waterfall(
            x=labels,
            y=values,
            measure=[
                "absolute",
                "relative",
                "relative",
                "relative",
                "relative",
                "relative",
                "relative",
                "total",
            ],
            connector={"line": {"color": "rgba(128,128,128,0.5)"}},
            text=[_money(value, analysis.currency) for value in values],
            textposition="outside",
        )
    )
    figure.update_layout(
        yaxis_title=analysis.currency,
        showlegend=False,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _exposure_chart(analysis: StatementAnalysis) -> go.Figure:
    groups = analysis.exposure_by_type[:10]
    figure = go.Figure(
        go.Bar(
            x=[group.value for group in reversed(groups)],
            y=[group.name for group in reversed(groups)],
            orientation="h",
            text=[_money(group.value, analysis.currency) for group in reversed(groups)],
            textposition="auto",
        )
    )
    figure.update_layout(
        xaxis_title=f"Gross exposure ({analysis.currency})",
        yaxis_title=None,
        showlegend=False,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _realized_performance_chart(
    analysis: StatementRangeAnalysis,
    currency: str,
) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[point.day for point in analysis.daily_performance],
            y=[point.estimated_cumulative_profit_loss for point in analysis.daily_performance],
            mode="lines",
            name="Estimated total P/L",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[point.day for point in analysis.daily_performance],
            y=[point.cumulative_profit_loss for point in analysis.daily_performance],
            mode="lines",
            name="Cumulative realized P/L",
        )
    )
    figure.update_layout(
        xaxis_title=None,
        yaxis_title=f"Cumulative P/L ({currency})",
        showlegend=True,
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


def _statement_growth(analysis: StatementRangeAnalysis) -> tuple[GrowthPoint, ...]:
    target_return = analysis.estimated_modified_dietz_return
    if target_return is None:
        return ()
    final_profit_loss = analysis.estimated_total_profit_loss
    scale = target_return / final_profit_loss if abs(final_profit_loss) >= 0.005 else 0.0
    growth = [GrowthPoint(day=analysis.start_date, value=10_000.0)]
    growth.extend(
        GrowthPoint(
            day=point.day,
            value=10_000 * (1 + (point.estimated_cumulative_profit_loss or 0) * scale),
        )
        for point in analysis.daily_performance
    )
    return tuple(growth)


def _growth_chart(
    points: tuple[GrowthPoint, ...],
    comparisons: dict[str, tuple[GrowthPoint, ...]] | None = None,
) -> go.Figure:
    figure = go.Figure(
        go.Scatter(
            x=[point.day for point in points],
            y=[point.value for point in points],
            mode="lines+markers",
            name="Account Statement",
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
        )
    )
    for symbol, comparison in (comparisons or {}).items():
        figure.add_trace(
            go.Scatter(
                x=[point.day for point in comparison],
                y=[point.value for point in comparison],
                mode="lines",
                name=symbol,
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f}<extra></extra>",
            )
        )
    figure.update_layout(
        xaxis_title=None,
        yaxis_title="Value of initial 10,000",
        showlegend=bool(comparisons),
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    return figure


@st.cache_data(ttl=3600, max_entries=16, show_spinner=False)
def _cached_benchmark_growth(
    symbol: str,
    start_date: object,
    end_date: object,
) -> tuple[GrowthPoint, ...]:
    return fetch_benchmark_growth(symbol, start_date, end_date)


def _render_performance_comparison(
    portfolio: tuple[GrowthPoint, ...],
    comparisons: dict[str, tuple[GrowthPoint, ...]],
) -> None:
    portfolio_return = portfolio[-1].value / portfolio[0].value - 1
    series = {"Account Statement": portfolio, **comparisons}
    rows = []
    for name, points in series.items():
        total_return = points[-1].value / points[0].value - 1
        drawdown = calculate_drawdown(points)
        rows.append(
            {
                "Series": name,
                "Total return": total_return * 100,
                "Final value": points[-1].value,
                "Maximum drawdown": drawdown.value * 100 if drawdown else None,
                "Account Statement minus series": (
                    None if name == "Account Statement" else (portfolio_return - total_return) * 100
                ),
            }
        )
    frame = pd.DataFrame(rows)
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Total return": st.column_config.NumberColumn(format="%.2f%%"),
            "Final value": st.column_config.NumberColumn(format="%.2f"),
            "Maximum drawdown": st.column_config.NumberColumn(format="%.2f%%"),
            "Account Statement minus series": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )


def _render_monthly_performance(points: tuple[GrowthPoint, ...]) -> None:
    monthly = monthly_performance(points)
    if not monthly:
        return
    with st.expander("Monthly performance", expanded=False):
        frame = pd.DataFrame(
            {
                "Month": [point.month.strftime("%Y-%m") for point in monthly],
                "Return": [point.return_value for point in monthly],
                "Growth of 10,000": [point.ending_value for point in monthly],
            }
        )
        st.bar_chart(frame.set_index("Month")["Return"])
        display_frame = frame.copy()
        display_frame["Return"] = display_frame["Return"].map(lambda value: f"{value:.2%}")
        st.dataframe(display_frame, hide_index=True, width="stretch")


def _render_position_contributions(contributions: tuple[PositionContribution, ...]) -> None:
    st.markdown("#### Closed-position contribution")
    if not contributions:
        st.info("No supported closed-position rows were found in the selected period.")
        return
    frame = pd.DataFrame(
        [
            {
                "Asset": item.asset,
                "Trading P/L": item.realized_profit_loss,
                "Included overnight fees and dividends": item.fees_and_dividends,
                "Total contribution": item.total_contribution,
                "Closed positions": item.closed_positions,
            }
            for item in contributions
        ]
    )
    chart_rows = frame.reindex(frame["Total contribution"].abs().sort_values(ascending=False).index).head(20)
    figure = go.Figure(
        go.Bar(
            x=chart_rows["Total contribution"],
            y=chart_rows["Asset"],
            orientation="h",
            marker_color=["#2ca02c" if value >= 0 else "#d62728" for value in chart_rows["Total contribution"]],
        )
    )
    figure.update_layout(
        xaxis_title="Contribution (USD)",
        yaxis_title=None,
        yaxis={"autorange": "reversed"},
        margin={"l": 20, "r": 20, "t": 10, "b": 20},
    )
    st.plotly_chart(figure, width="stretch")
    st.dataframe(frame, hide_index=True, width="stretch")
    st.caption(
        "This breakdown uses exact rows from eToro Closed Positions whose close date is inside "
        "the selected range. Open-position valuation changes are not assigned to individual assets."
    )


def _format_period(start: object, end: object) -> str:
    if start is None and end is None:
        return "Unknown"
    start_text = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else "?"
    end_text = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else "?"
    return f"{start_text} – {end_text}"


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def _arrow_safe_frame(rows: object, columns: object) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns)
    for column in frame.columns:
        populated = frame[column].dropna()
        inferred = pd.api.types.infer_dtype(populated, skipna=True)
        if inferred.startswith("mixed"):
            frame[column] = frame[column].map(
                lambda value: "" if value is None else str(value)
            )
    return frame
