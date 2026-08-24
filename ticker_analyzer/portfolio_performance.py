from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from math import isfinite

import pandas as pd

from ticker_analyzer.returns_table import GrowthPoint

MAX_COMPARISON_SYMBOLS = 10


class BenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True)
class Drawdown:
    value: float
    peak_date: date
    trough_date: date


@dataclass(frozen=True)
class MonthlyPerformance:
    month: date
    return_value: float
    ending_value: float


def parse_comparison_symbols(
    value: str,
    *,
    maximum: int = MAX_COMPARISON_SYMBOLS,
) -> tuple[str, ...]:
    """Normalize a comma, semicolon, or whitespace separated Yahoo ticker list."""
    normalized = value.replace(",", " ").replace(";", " ")
    symbols = tuple(dict.fromkeys(part.strip().upper() for part in normalized.split() if part.strip()))
    if len(symbols) > maximum:
        raise BenchmarkError(f"Enter no more than {maximum} comparison tickers.")
    return symbols


def fetch_benchmark_growth(
    symbol: str,
    start_date: date,
    end_date: date,
    *,
    initial_capital: float = 10_000.0,
) -> tuple[GrowthPoint, ...]:
    import yfinance as yf

    from ticker_analyzer.data_provider import retry_transient

    normalized = symbol.strip().upper()
    if not normalized:
        raise BenchmarkError("Enter a benchmark ticker.")
    try:
        history = retry_transient(
            lambda: yf.Ticker(normalized).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                actions=False,
            )
        )
    except Exception as exc:
        raise BenchmarkError(f"Benchmark data could not be downloaded: {exc}") from exc
    return benchmark_growth_from_history(history, start_date, end_date, initial_capital=initial_capital)


def benchmark_growth_from_history(
    history: pd.DataFrame,
    start_date: date,
    end_date: date,
    *,
    initial_capital: float = 10_000.0,
) -> tuple[GrowthPoint, ...]:
    if history.empty or "Close" not in history:
        raise BenchmarkError("No benchmark prices are available for the selected range.")
    closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
    closes = closes[(closes.index.date >= start_date) & (closes.index.date <= end_date)]
    if closes.empty or not isfinite(float(closes.iloc[0])) or float(closes.iloc[0]) <= 0:
        raise BenchmarkError("No usable benchmark prices are available for the selected range.")
    base = float(closes.iloc[0])
    points = [GrowthPoint(day=start_date, value=initial_capital)]
    points.extend(
        GrowthPoint(day=index.date(), value=initial_capital * float(value) / base)
        for index, value in closes.items()
    )
    return tuple(points)


def calculate_drawdown(points: Iterable[GrowthPoint]) -> Drawdown | None:
    ordered = tuple(points)
    if not ordered:
        return None
    peak_value = ordered[0].value
    peak_date = ordered[0].day
    worst = 0.0
    worst_peak = peak_date
    worst_trough = peak_date
    for point in ordered:
        if point.value > peak_value:
            peak_value = point.value
            peak_date = point.day
        if peak_value <= 0:
            continue
        drawdown = point.value / peak_value - 1
        if drawdown < worst:
            worst = drawdown
            worst_peak = peak_date
            worst_trough = point.day
    return Drawdown(value=worst, peak_date=worst_peak, trough_date=worst_trough)


def monthly_performance(points: Iterable[GrowthPoint]) -> tuple[MonthlyPerformance, ...]:
    ordered = sorted(points, key=lambda point: point.day)
    if len(ordered) < 2:
        return ()
    month_ends: dict[tuple[int, int], GrowthPoint] = {}
    for point in ordered:
        month_ends[(point.day.year, point.day.month)] = point
    results = []
    previous = ordered[0].value
    for key in sorted(month_ends):
        point = month_ends[key]
        if point.day == ordered[0].day:
            continue
        return_value = point.value / previous - 1 if previous > 0 else 0.0
        results.append(MonthlyPerformance(date(key[0], key[1], 1), return_value, point.value))
        previous = point.value
    return tuple(results)
