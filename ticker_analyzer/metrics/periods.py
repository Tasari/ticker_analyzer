"""Dated annual/TTM observations shared by current and historical valuation."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ticker_analyzer.metrics.utils import row_values


@dataclass
class ValuationPeriods:
    observations: pd.DataFrame

    def current(self) -> tuple[float | None, str]:
        if self.observations.empty:
            return None, "statement period unavailable"
        row = self.observations.sort_values(["period", "priority"]).iloc[-1]
        return float(row.value), f"{row.basis} ending {row.period.date()}"

    def historical(self) -> pd.Series:
        if self.observations.empty:
            return pd.Series(dtype=float)
        events = self.observations.sort_values(["available", "period", "priority"])
        values = {}
        latest = (pd.Timestamp.min, -1)
        for row in events.itertuples():
            candidate = (row.period, row.priority)
            if candidate >= latest:
                latest = candidate
                values[row.available] = row.value
        return pd.Series(values, dtype=float).sort_index()


def free_cash_flow_periods(annual: pd.DataFrame, quarterly: pd.DataFrame) -> ValuationPeriods:
    frames = []
    for original in (annual, quarterly):
        frame = original.copy()
        if not frame.empty:
            cfo = row_values(frame, ["Operating Cash Flow", "Total Cash From Operating Activities"])
            capex = row_values(frame, ["Capital Expenditure", "Capital Expenditures"])
            derived = cfo - capex.abs()
            explicit = row_values(frame, ["Free Cash Flow"])
            frame.loc["Free Cash Flow"] = explicit.combine_first(derived).reindex(frame.columns)
        frames.append(frame)
    return valuation_periods(*frames, ["Free Cash Flow"])


def valuation_periods(annual: pd.DataFrame, quarterly: pd.DataFrame, names: list[str], *, stock: bool = False) -> ValuationPeriods:
    observations = []
    for frame, is_quarterly in ((annual, False), (quarterly, True)):
        if frame.empty:
            continue
        dates = pd.DatetimeIndex(pd.to_datetime(frame.columns)).tz_localize(None)
        values = row_values(frame, names)
        if values.empty:
            continue
        values.index = pd.DatetimeIndex(pd.to_datetime(values.index)).tz_localize(None)
        values = values.reindex(dates).sort_index()
        values = values[~values.index.duplicated(keep="last")]
        for position, (period, value) in enumerate(values.items()):
            required_dates = [period]
            if is_quarterly and not stock:
                window = values.iloc[max(0, position - 3):position + 1]
                gaps = window.index.to_series().diff().dropna().dt.days
                if len(window) != 4 or window.isna().any() or not gaps.between(60, 120).all():
                    continue
                value = window.sum()
                required_dates = list(window.index)
            if pd.isna(value):
                continue
            filed = frame.attrs.get("filed_dates", {})
            availability = []
            for date in required_dates:
                explicit = pd.to_datetime(filed.get(date), errors="coerce")
                availability.append(
                    pd.Timestamp(explicit).tz_localize(None) if pd.notna(explicit)
                    else date + pd.Timedelta(days=90)
                )
            observations.append({
                "period": period, "available": max(availability), "value": float(value),
                "priority": int(is_quarterly),
                "basis": ("Quarterly balance" if is_quarterly else "Annual balance") if stock
                else ("TTM (4 consecutive quarters)" if is_quarterly else "Annual fallback"),
            })
    return ValuationPeriods(pd.DataFrame(observations))
