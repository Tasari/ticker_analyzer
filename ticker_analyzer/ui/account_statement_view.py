from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ticker_analyzer.account_statement import (
    AccountStatementError,
    StatementAnalysis,
    StatementOverview,
    StatementRangeAnalysis,
    analyze_account_statement,
    analyze_statement_range,
    inspect_account_statement,
    read_statement_sheet,
)


def render_account_statement() -> None:
    st.subheader("Account Statement")
    st.caption(
        "Upload an eToro XLSX account statement to analyze its period-end portfolio snapshot. "
        "The file is processed in memory for this session and is not added to the repository."
    )
    uploaded = st.file_uploader(
        "Account statement",
        type=["xlsx"],
        accept_multiple_files=False,
        help="Current importer supports eToro account statements up to 10 MB.",
    )
    if uploaded is None:
        st.info("Choose an eToro account statement in XLSX format to begin.")
        return

    payload = uploaded.getvalue()
    try:
        overview = inspect_account_statement(payload)
    except AccountStatementError as exc:
        st.error(str(exc))
        return

    st.success(f"Loaded {uploaded.name}")
    analysis_tab, preview_tab = st.tabs(["Analysis", "Data preview"])
    with analysis_tab:
        _render_analysis(payload)
    with preview_tab:
        _render_data_preview(payload, overview)


def _render_analysis(payload: bytes) -> None:
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
    try:
        range_analysis = analyze_statement_range(payload, selected_start, selected_end)
    except AccountStatementError as exc:
        st.error(str(exc))
        return
    full_period = (
        selected_start == statement_start
        and selected_end == statement_end
    )

    st.markdown("#### Selected-period performance")
    if full_period:
        _render_full_period_performance(analysis)
    else:
        _render_partial_period_performance(range_analysis, analysis.currency)

    st.markdown("#### Portfolio snapshot")
    st.caption(
        f"Snapshot as of {analysis.end_date:%Y-%m-%d}; it does not move with the selected range "
        "and is not refreshed with live prices."
    )
    snapshot = st.columns(3)
    snapshot[0].metric("Portfolio value", _money(analysis.ending_unrealized_equity, analysis.currency))
    snapshot[1].metric("Open positions", f"{analysis.open_positions:,}")
    snapshot[2].metric(
        "Gross exposure",
        _money(analysis.long_exposure + analysis.short_exposure, analysis.currency),
        help="Absolute exposure from Holdings; this is not the portfolio equity value.",
    )
    _render_exposure_and_cash_flows(analysis, selected_start, selected_end)


def _render_full_period_performance(analysis: StatementAnalysis) -> None:
    currency = analysis.currency
    primary = st.columns(4)
    primary[0].metric("Total P/L", _money(analysis.total_profit_loss, currency))
    primary[1].metric(
        "ROI",
        _percent(analysis.simple_roi),
        help="Total P/L divided by beginning equity plus positive contributions.",
    )
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
    st.caption(
        "Modified Dietz weights each external cash flow by how long it remained invested. "
        "A true TWR requires portfolio valuations at cash-flow boundaries."
    )


def _render_partial_period_performance(
    range_analysis: StatementRangeAnalysis,
    currency: str,
) -> None:
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
