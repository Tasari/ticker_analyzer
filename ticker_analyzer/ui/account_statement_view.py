from __future__ import annotations

import pandas as pd
import streamlit as st

from ticker_analyzer.account_statement import (
    AccountStatementError,
    inspect_account_statement,
    read_statement_sheet,
)


def render_account_statement() -> None:
    st.subheader("Account Statement Import")
    st.caption(
        "Upload an eToro XLSX account statement to inspect its contents. "
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
    period = _format_period(overview.start_date, overview.end_date)
    metric_columns = st.columns(3)
    metric_columns[0].metric("Currency", overview.currency or "Unknown")
    metric_columns[1].metric("Statement period", period)
    metric_columns[2].metric("Worksheets", len(overview.sheets))

    sheet_names = [sheet.name for sheet in overview.sheets]
    selected_sheet = st.selectbox("Worksheet preview", sheet_names)
    selected_info = next(sheet for sheet in overview.sheets if sheet.name == selected_sheet)
    st.caption(
        f"{selected_info.data_rows:,} data rows · {selected_info.columns:,} columns"
    )
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


def _format_period(start: object, end: object) -> str:
    if start is None and end is None:
        return "Unknown"
    start_text = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else "?"
    end_text = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else "?"
    return f"{start_text} – {end_text}"


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
