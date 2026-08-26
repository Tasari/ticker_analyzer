from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt

import pandas as pd

TRAILING_RETURN_PERIODS = (
    ("1M", 1),
    ("3M", 3),
    ("6M", 6),
    ("1Y", 12),
    ("2Y", 24),
    ("3Y", 36),
    ("4Y", 48),
    ("5Y", 60),
)


class SimulationError(ValueError):
    pass


@dataclass(frozen=True)
class SimulationPosition:
    ticker: str
    weight: float
    allocation: float
    entry_date: date | None
    entry_price: float | None
    shares: float
    final_price: float | None
    final_value: float
    profit_loss: float
    return_value: float
    trailing_returns: tuple[tuple[str, float | None], ...]
    status: str


@dataclass(frozen=True)
class SimulationResult:
    start_date: date
    end_date: date
    initial_capital: float
    final_value: float
    profit_loss: float
    return_value: float
    cagr: float | None
    maximum_drawdown: float
    annualized_volatility: float | None
    portfolio_values: pd.Series
    position_values: pd.DataFrame
    positions: tuple[SimulationPosition, ...]


def simulate_buy_and_hold(
    price_histories: dict[str, pd.Series],
    weights: dict[str, float],
    initial_capital: float,
    start_date: date,
    end_date: date,
) -> SimulationResult:
    if initial_capital <= 0:
        raise SimulationError("Initial capital must be positive.")
    if end_date <= start_date:
        raise SimulationError("Simulation end date must be after its start date.")
    if not weights:
        raise SimulationError("Select at least one ticker for the simulation.")
    if any(weight < 0 for weight in weights.values()):
        raise SimulationError("Portfolio weights cannot be negative.")
    weight_total = sum(weights.values())
    if abs(weight_total - 1.0) > 0.0001:
        raise SimulationError("Portfolio weights must add up to 100%.")

    calendar = pd.DatetimeIndex(pd.date_range(start_date, end_date, freq="B"))
    boundary_dates = pd.DatetimeIndex([pd.Timestamp(start_date), pd.Timestamp(end_date)])
    calendar = calendar.union(boundary_dates).sort_values()
    position_values: dict[str, pd.Series] = {}
    positions: list[SimulationPosition] = []

    for ticker, weight in weights.items():
        allocation = initial_capital * weight
        history = price_histories.get(ticker)
        prices = _clean_prices(history, start_date, end_date)
        trailing_returns = calculate_trailing_returns(history, end_date)
        if prices.empty:
            values = pd.Series(allocation, index=calendar, dtype=float)
            position_values[ticker] = values
            positions.append(
                SimulationPosition(
                    ticker=ticker,
                    weight=weight,
                    allocation=allocation,
                    entry_date=None,
                    entry_price=None,
                    shares=0.0,
                    final_price=None,
                    final_value=allocation,
                    profit_loss=0.0,
                    return_value=0.0,
                    trailing_returns=trailing_returns,
                    status="Cash: no usable prices",
                )
            )
            continue

        entry_timestamp = prices.index[0]
        entry_price = float(prices.iloc[0])
        shares = allocation / entry_price
        aligned = prices.reindex(calendar).ffill()
        values = pd.Series(allocation, index=calendar, dtype=float)
        invested = calendar >= entry_timestamp
        values.loc[invested] = aligned.loc[invested] * shares
        final_price = float(prices.iloc[-1])
        final_value = float(values.iloc[-1])
        position_values[ticker] = values
        positions.append(
            SimulationPosition(
                ticker=ticker,
                weight=weight,
                allocation=allocation,
                entry_date=entry_timestamp.date(),
                entry_price=entry_price,
                shares=shares,
                final_price=final_price,
                final_value=final_value,
                profit_loss=final_value - allocation,
                return_value=final_value / allocation - 1 if allocation > 0 else 0.0,
                trailing_returns=trailing_returns,
                status="Invested",
            )
        )

    position_frame = pd.DataFrame(position_values, index=calendar)
    portfolio = position_frame.sum(axis=1)
    final_value = float(portfolio.iloc[-1])
    return_value = final_value / initial_capital - 1
    elapsed_days = (end_date - start_date).days
    cagr = (1 + return_value) ** (365.2425 / elapsed_days) - 1 if return_value > -1 else None
    running_peak = portfolio.cummax()
    drawdowns = portfolio.div(running_peak).sub(1)
    returns = portfolio.pct_change().dropna()
    volatility = float(returns.std(ddof=1) * sqrt(252)) if len(returns) >= 2 else None
    return SimulationResult(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_value=final_value,
        profit_loss=final_value - initial_capital,
        return_value=return_value,
        cagr=cagr,
        maximum_drawdown=float(drawdowns.min()),
        annualized_volatility=volatility,
        portfolio_values=portfolio,
        position_values=position_frame,
        positions=tuple(positions),
    )


def calculate_trailing_returns(
    series: pd.Series | None,
    end_date: date,
) -> tuple[tuple[str, float | None], ...]:
    prices = _normalize_prices(series)
    if prices.empty:
        return tuple((label, None) for label, _ in TRAILING_RETURN_PERIODS)
    prices = prices[prices.index.date <= end_date]
    if prices.empty:
        return tuple((label, None) for label, _ in TRAILING_RETURN_PERIODS)
    final_price = float(prices.iloc[-1])
    final_timestamp = prices.index[-1]
    returns: list[tuple[str, float | None]] = []
    for label, months in TRAILING_RETURN_PERIODS:
        boundary = pd.Timestamp(end_date) - pd.DateOffset(months=months)
        boundary_prices = prices[prices.index <= boundary]
        value = None
        if not boundary_prices.empty and final_timestamp > boundary:
            value = final_price / float(boundary_prices.iloc[-1]) - 1
        returns.append((label, value))
    return tuple(returns)


def _normalize_prices(series: pd.Series | None) -> pd.Series:
    if series is None or series.empty:
        return pd.Series(dtype=float)
    prices = pd.to_numeric(series, errors="coerce").dropna()
    index = pd.to_datetime(prices.index, errors="coerce", utc=True).tz_convert(None).normalize()
    prices.index = index
    prices = prices[~prices.index.duplicated(keep="last")].sort_index()
    return prices[prices > 0]


def _clean_prices(series: pd.Series | None, start_date: date, end_date: date) -> pd.Series:
    prices = _normalize_prices(series)
    if prices.empty:
        return prices
    prices = prices[(prices.index.date >= start_date) & (prices.index.date <= end_date)]
    return prices
