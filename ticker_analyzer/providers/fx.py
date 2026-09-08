"""Bounded shared FX history cache; rates always mean target units per source unit."""
from __future__ import annotations

import time
from datetime import UTC, datetime
from functools import lru_cache
from threading import RLock

import pandas as pd
import requests

from ticker_analyzer.markets import currency_convention

_LOCK = RLock()


def fx_history(source: str, target: str, start: str, end: str) -> pd.Series:
    source = currency_convention(source)[0]
    target = currency_convention(target)[0]
    if not source or not target:
        return pd.Series(dtype=float)
    if source == target:
        return pd.Series(1.0, index=pd.date_range(start, end))
    # Coalesce concurrent scanner requests and cache failures too, avoiding a
    # new request per company when a currency endpoint is unavailable.
    with _LOCK:
        return _cached_history(source, target, start[:4], end[:4], int(time.time() // 3600)).copy()


@lru_cache(maxsize=64)
def _cached_history(source: str, target: str, start_year: str, end_year: str, bucket: int) -> pd.Series:
    direct = _download_history(source, target, start_year, end_year)
    if source == "USD" or target == "USD":
        return direct
    # Less common direct crosses can expose only a few recent observations.
    # Fill their gaps through USD using contemporaneous legs, never future FX.
    left = _cached_history(source, "USD", start_year, end_year, bucket)
    right = _cached_history("USD", target, start_year, end_year, bucket)
    dates = left.index.union(right.index).sort_values()
    if len(dates) == 0:
        return direct
    cross = left.reindex(dates, method="ffill", tolerance=pd.Timedelta(days=7)) * right.reindex(
        dates, method="ffill", tolerance=pd.Timedelta(days=7),
    )
    return direct.combine_first(cross.dropna()).sort_index()


def _download_history(source: str, target: str, start_year: str, end_year: str) -> pd.Series:
    for left, right, inverse in ((source, target, False), (target, source, True)):
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{left}{right}=X",
                params={
                    "period1": int(datetime(int(start_year), 1, 1, tzinfo=UTC).timestamp()),
                    "period2": int(datetime(int(end_year) + 1, 1, 1, tzinfo=UTC).timestamp()),
                    "interval": "1d",
                },
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
            )
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            values = pd.Series(
                result["indicators"]["quote"][0]["close"],
                index=pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_localize(None).normalize(),
                dtype=float,
            ).dropna()
            values = values[(values > 0) & (values < float("inf"))]
            values = values[~values.index.duplicated(keep="last")].sort_index()
            if not values.empty:
                return 1 / values if inverse else values
        except (requests.RequestException, KeyError, TypeError, ValueError, IndexError):
            continue
    return pd.Series(dtype=float)


def rates_on_dates(source: str, target: str, dates: pd.DatetimeIndex) -> pd.Series:
    days = pd.to_datetime(dates).tz_localize(None).normalize()
    if len(days) == 0 or not source or not target:
        return pd.Series(index=dates, dtype=float)
    if source == target:
        return pd.Series(1.0, index=dates)
    history = fx_history(source, target, str(days.min().date() - pd.Timedelta(days=7)), str(days.max().date()))
    aligned = history.reindex(days, method="ffill", tolerance=pd.Timedelta(days=7))
    return pd.Series(aligned.to_numpy(), index=dates)


def exchange_rate(source: str, target: str, day: str | None = None) -> float | None:
    day = day or datetime.now(UTC).date().isoformat()
    value = rates_on_dates(source, target, pd.DatetimeIndex([day])).iloc[0]
    return float(value) if pd.notna(value) else None
