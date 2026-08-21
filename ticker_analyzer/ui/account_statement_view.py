from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ticker_analyzer.account_statement import (
    AccountStatementError,
    StatementAnalysis,
    StatementOverview,
    analyze_account_statement,
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

    st.caption(
        f"Portfolio snapshot as of {analysis.end_date:%Y-%m-%d}. "
        "Values come from the statement and are not refreshed with live prices."
    )
    currency = analysis.currency
    primary = st.columns(4)
    primary[0].metric(
        "Portfolio value",
        _money(analysis.ending_unrealized_equity, currency),
        delta=_money(analysis.total_profit_loss, currency),
        help="Ending Unrealized Equity. The delta is total P/L after external cash flows.",
    )
    primary[1].metric(
        "Total P/L",
        _money(analysis.total_profit_loss, currency),
        help="Ending equity minus beginning equity and net external cash flows.",
    )
    primary[2].metric(
        "ROI",
        _percent(analysis.simple_roi),
        help="Total P/L divided by beginning equity plus positive contributions.",
    )
    primary[3].metric(
        "Estimated TWR",
        _percent(analysis.modified_dietz_return),
        help=(
            "Modified Dietz estimate using the exact timing of dated external cash flows. "
            "It is not a true daily-valued TWR."
        ),
    )

    secondary = st.columns(4)
    secondary[0].metric(
        "Annualized ROI",
        _percent(analysis.annualized_roi),
        help="ROI annualized over the exact statement duration (CAGR-style).",
    )
    secondary[1].metric("Net external flows", _money(analysis.net_external_flows, currency))
    secondary[2].metric("Open positions", f"{analysis.open_positions:,}")
    secondary[3].metric(
        "Gross exposure",
        _money(analysis.long_exposure + analysis.short_exposure, currency),
        help="Absolute exposure from Holdings; this is not the portfolio equity value.",
    )

    for warning in analysis.warnings:
        st.warning(warning)

    st.markdown("#### P/L bridge")
    st.plotly_chart(_profit_loss_waterfall(analysis), width="stretch")

    exposure_col, cash_flow_col = st.columns(2)
    with exposure_col:
        st.markdown("#### Exposure by asset type")
        if analysis.exposure_by_type:
            st.plotly_chart(_exposure_chart(analysis), width="stretch")
            st.caption(
                f"Long {_money(analysis.long_exposure, currency)} · "
                f"Short {_money(analysis.short_exposure, currency)}. "
                "Exposure may exceed equity because positions can be leveraged or copied."
            )
        else:
            st.info("No holdings exposure was available in this statement.")
    with cash_flow_col:
        st.markdown("#### External cash flows")
        if analysis.cash_flows:
            cash_flow_frame = pd.DataFrame(
                [
                    {
                        "Date": flow.occurred_at,
                        "Type": flow.kind,
                        f"Amount ({currency})": flow.amount,
                        "Date estimated": flow.estimated_date,
                    }
                    for flow in analysis.cash_flows
                ]
            )
            st.dataframe(cash_flow_frame, width="stretch", hide_index=True)
        else:
            st.info("No external cash flows occurred during this statement period.")

    st.caption(
        "Modified Dietz weights each external cash flow by how long it remained invested. "
        "A true TWR requires portfolio valuations at cash-flow boundaries, which this export does not provide."
    )


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
