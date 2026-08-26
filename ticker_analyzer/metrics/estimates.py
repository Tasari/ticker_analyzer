from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.metrics.utils import clean_number, percent_change


def estimate_growth(
    info: dict[str, Any],
    kind: str,
    estimate_table: pd.DataFrame | None = None,
    growth_estimates: pd.DataFrame | None = None,
    *,
    min_analysts: int = 5,
) -> float | None:
    positive_only = kind == "eps"
    structured_growth = estimate_growth_from_table(
        estimate_table,
        min_analysts=min_analysts,
        positive_only=positive_only,
    )
    if structured_growth is not None:
        return structured_growth
    if positive_only and estimate_pair_has_non_positive_value(estimate_table, min_analysts=min_analysts):
        return None
    if positive_only:
        info_current = clean_number(info.get("epsCurrentYear"))
        info_next_year = clean_number(info.get("epsNextYear"))
        if (
            info_current is not None
            and info_next_year is not None
            and (info_current <= 0 or info_next_year <= 0)
        ):
            return None
    growth_estimate = growth_from_estimates(growth_estimates, period="+1y")
    if growth_estimate is not None:
        return growth_estimate
    if kind == "revenue":
        current = clean_number(info.get("revenueCurrentYear"))
        next_year = clean_number(info.get("revenueNextYear"))
        growth = clean_number(info.get("revenueGrowth"))
    else:
        current = clean_number(info.get("epsCurrentYear"))
        next_year = clean_number(info.get("epsNextYear"))
        growth = clean_number(info.get("earningsGrowth"))
    if positive_only and current is not None and next_year is not None and (current <= 0 or next_year <= 0):
        return None
    if current not in (None, 0) and next_year is not None:
        return percent_change(next_year, current)
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def estimate_growth_from_table(
    table: pd.DataFrame | None,
    *,
    min_analysts: int,
    positive_only: bool = False,
) -> float | None:
    if table is None or table.empty:
        return None
    current = estimate_row(table, "0y")
    next_year = estimate_row(table, "+1y")
    if current is None or next_year is None:
        return None
    analysts = clean_number(next_year.get("numberOfAnalysts"))
    if analysts is not None and analysts < min_analysts:
        return None
    current_avg = clean_number(current.get("avg"))
    next_avg = clean_number(next_year.get("avg"))
    if positive_only and current_avg is not None and next_avg is not None and (current_avg <= 0 or next_avg <= 0):
        return None
    if current_avg not in (None, 0) and next_avg is not None:
        return percent_change(next_avg, current_avg)
    growth = clean_number(next_year.get("growth"))
    if growth is not None:
        return growth * 100 if abs(growth) < 2 else growth
    return None


def estimate_growth_note(
    kind: str,
    estimate_table: pd.DataFrame | None,
    info: dict[str, Any] | None = None,
    *,
    min_analysts: int = 5,
) -> str:
    if kind == "eps" and estimate_pair_has_non_positive_value(estimate_table, min_analysts=min_analysts):
        current, next_year = estimate_pair(estimate_table)
        if current is not None and next_year is not None and current <= 0 < next_year:
            return "EPS turnaround: estimates move from non-positive to positive; excluded from percentage-growth scoring"
        return "EPS estimate growth excluded because current or next-year EPS is non-positive"
    if kind == "eps" and info:
        current = clean_number(info.get("epsCurrentYear"))
        next_year = clean_number(info.get("epsNextYear"))
        if current is not None and next_year is not None and current <= 0 < next_year:
            return "EPS turnaround: estimates move from non-positive to positive; excluded from percentage-growth scoring"
        if current is not None and next_year is not None and (current <= 0 or next_year <= 0):
            return "EPS estimate growth excluded because current or next-year EPS is non-positive"
    source = "earnings" if kind == "eps" else "revenue"
    return f"Uses structured yfinance {source}_estimate when enough analysts are available, then falls back to other yfinance estimate fields"


def estimate_pair_has_non_positive_value(table: pd.DataFrame | None, *, min_analysts: int) -> bool:
    if table is None or table.empty:
        return False
    next_year = estimate_row(table, "+1y")
    if next_year is None:
        return False
    analysts = clean_number(next_year.get("numberOfAnalysts"))
    if analysts is not None and analysts < min_analysts:
        return False
    current, future = estimate_pair(table)
    return current is not None and future is not None and (current <= 0 or future <= 0)


def estimate_pair(table: pd.DataFrame | None) -> tuple[float | None, float | None]:
    if table is None or table.empty:
        return None, None
    current = estimate_row(table, "0y")
    next_year = estimate_row(table, "+1y")
    if current is None or next_year is None:
        return None, None
    return clean_number(current.get("avg")), clean_number(next_year.get("avg"))


def estimate_row(table: pd.DataFrame, period: str) -> dict[str, Any] | None:
    if period not in table.index:
        return None
    row = table.loc[period]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return row.to_dict()


def growth_from_estimates(table: pd.DataFrame | None, *, period: str) -> float | None:
    if table is None or table.empty:
        return None
    row = estimate_row(table, period)
    if row is None:
        return None
    growth = clean_number(row.get("stockTrend"))
    if growth is None:
        return None
    return growth * 100 if abs(growth) < 2 else growth


def target_upside(info: dict[str, Any], analyst_targets: dict[str, Any]) -> float | None:
    price = clean_number(info.get("currentPrice") or info.get("regularMarketPrice"))
    target = clean_number(
        analyst_targets.get("mean")
        or analyst_targets.get("targetMeanPrice")
        or info.get("targetMeanPrice")
        or info.get("targetMedianPrice")
    )
    if price in (None, 0) or target is None:
        return None
    return percent_change(target, price)
