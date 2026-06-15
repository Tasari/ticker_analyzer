from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def metric_value(value: float | None, note: str = "") -> dict[str, Any]:
    return {"value": clean_number(value), "note": note}


def clean_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def scale_billions(value: Any) -> float | None:
    number = clean_number(value)
    if number is None:
        return None
    return number / 1_000_000_000


def row_values(frame: pd.DataFrame, names: list[str]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=float)
    lowered = {str(index).lower(): index for index in frame.index}
    for name in names:
        index = lowered.get(name.lower())
        if index is not None:
            values = pd.to_numeric(frame.loc[index], errors="coerce").dropna()
            return values
    return pd.Series(dtype=float)


def latest_row_value(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if values.empty:
        return None
    return clean_number(values.iloc[-1])


def latest_statement_growth(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if len(values) < 2:
        return None
    return percent_change(values.iloc[-1], values.iloc[-2])


def statement_value_years_ago(frame: pd.DataFrame, names: list[str], years_ago: int) -> float | None:
    values = row_values(frame, names)
    required_length = years_ago + 1
    if len(values) < required_length:
        return None
    return clean_number(values.iloc[-required_length])


def sum_recent(frame: pd.DataFrame, names: list[str], periods: int) -> float | None:
    values = row_values(frame, names)
    if len(values) < periods:
        return None
    return clean_number(values.iloc[-periods:].sum())


def sum_window(frame: pd.DataFrame, names: list[str], start: int, end: int) -> float | None:
    values = row_values(frame, names)
    if len(values) < end:
        return None
    return clean_number(values.iloc[-end:-start].sum())


def percent_change(current: Any, previous: Any) -> float | None:
    current = clean_number(current)
    previous = clean_number(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / abs(previous)) * 100


def cagr_pct(current: Any, previous: Any, years: int) -> float | None:
    current = clean_number(current)
    previous = clean_number(previous)
    if current is None or previous is None or years <= 0:
        return None
    if current <= 0 or previous <= 0:
        return None
    return ((current / previous) ** (1 / years) - 1) * 100


def ttm_range_cagr(
    frame: pd.DataFrame,
    names: list[str],
    years: int,
    *,
    fallback_current: Any = None,
    fallback_base: Any = None,
) -> tuple[float | None, str]:
    values = row_values(frame, names)
    needed = 4 * (years + 1)
    if len(values) >= needed:
        current_ttm = clean_number(values.iloc[-4:].sum())
        past_ttm = clean_number(values.iloc[-needed:-needed + 4].sum())
        return cagr_pct(current_ttm, past_ttm, years), f"TTM vs TTM CAGR over {years} year(s)"
    return (
        cagr_pct(fallback_current, fallback_base, years),
        f"Annual fallback over {years} year(s); yfinance did not provide the {needed} quarters required for TTM vs TTM",
    )


def range_median_note(years: int, observations: int, prefix: str = "") -> str:
    detail = f"Selected {years}Y range; median from {observations} available annual observation(s)"
    return f"{prefix}; {detail}" if prefix else detail


def percentage_change_from_history(history: pd.DataFrame) -> float | None:
    if history.empty or "Close" not in history:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2 or close.iloc[0] == 0:
        return None
    return percent_change(close.iloc[-1], close.iloc[0])


def momentum_12_1(history: pd.DataFrame) -> float | None:
    if history.empty or "Close" not in history:
        return None
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 2:
        return None
    monthly = close.resample("ME").last().dropna()
    if len(monthly) < 13:
        return None
    return percent_change(monthly.iloc[-2], monthly.iloc[-13])


def range_ratio_metric(observations: list[float], years: int, prefix: str = "") -> dict[str, Any]:
    minimum = 1 if years == 1 else 2
    count = len(observations)
    note = range_median_note(years, count, prefix)
    if count < minimum:
        return metric_value(None, f"{note}; requires at least {minimum} observation(s)")
    return metric_value(median_or_none(observations), note)


def aligned_ratio_observations(
    numerators: pd.Series,
    denominators: pd.Series,
    years: int,
    *,
    multiplier: float = 1.0,
) -> list[float]:
    values: list[float] = []
    for date, numerator_value in numerators.tail(years).items():
        numerator = clean_number(numerator_value)
        denominator = value_on_or_before(denominators, date)
        if numerator is None or denominator in (None, 0):
            continue
        values.append(numerator / denominator * multiplier)
    return values


def statement_ratio_median(
    numerator_frame: pd.DataFrame,
    numerator_names: list[str],
    denominator_frame: pd.DataFrame,
    denominator_names: list[str],
    years: int,
    *,
    multiplier: float = 1.0,
    absolute_denominator: bool = False,
    zero_denominator_cap: float | None = None,
) -> float | None:
    return median_or_none(
        statement_ratio_observations(
            numerator_frame,
            numerator_names,
            denominator_frame,
            denominator_names,
            years,
            multiplier=multiplier,
            absolute_denominator=absolute_denominator,
            zero_denominator_cap=zero_denominator_cap,
        )
    )


def statement_ratio_observations(
    numerator_frame: pd.DataFrame,
    numerator_names: list[str],
    denominator_frame: pd.DataFrame,
    denominator_names: list[str],
    years: int,
    *,
    multiplier: float = 1.0,
    absolute_denominator: bool = False,
    zero_denominator_cap: float | None = None,
) -> list[float]:
    numerators = row_values(numerator_frame, numerator_names)
    denominators = row_values(denominator_frame, denominator_names)
    if numerators.empty or denominators.empty:
        return []
    ratios: list[float] = []
    for date, numerator_value in numerators.tail(years).items():
        numerator = clean_number(numerator_value)
        denominator = value_on_or_before(denominators, date)
        if numerator is None or denominator is None:
            continue
        denominator = abs(denominator) if absolute_denominator else denominator
        if denominator == 0:
            if zero_denominator_cap is not None and numerator > 0:
                ratios.append(zero_denominator_cap)
            continue
        if denominator < 0:
            continue
        ratios.append(numerator / denominator * multiplier)
    return ratios


def median_or_none(values: list[float]) -> float | None:
    cleaned = [number for value in values if (number := clean_number(value)) is not None]
    if not cleaned:
        return None
    return float(np.median(cleaned))


def value_on_or_before(values: pd.Series, date: Any) -> float | None:
    if values.empty:
        return None
    series = pd.to_numeric(values, errors="coerce").dropna()
    if series.empty:
        return None
    try:
        index = pd.to_datetime(series.index)
        target = pd.Timestamp(date)
        dated = pd.Series(series.to_numpy(), index=index).sort_index()
        eligible = dated[dated.index <= target]
        if eligible.empty:
            return None
        return clean_number(eligible.iloc[-1])
    except Exception:
        return clean_number(series.iloc[-1])
