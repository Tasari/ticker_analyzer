from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil, sqrt
from typing import Any, Literal

import pandas as pd

from ticker_analyzer.portfolio.simulation import (
    SimulationError,
    SimulationPosition,
    _normalize_prices,
    calculate_trailing_returns,
)

Frequency = Literal["none", "monthly", "quarterly", "annual"]
DividendPolicy = Literal["reinvest", "cash"]

FREQUENCY_MONTHS: dict[Frequency, int | None] = {
    "none": None,
    "monthly": 1,
    "quarterly": 3,
    "annual": 12,
}


@dataclass(frozen=True)
class SimulationAssumptions:
    contribution_amount: float = 0.0
    contribution_frequency: Frequency = "monthly"
    rebalance_frequency: Frequency = "none"
    commission_percent: float = 0.0
    commission_fixed: float = 0.0
    spread_percent: float = 0.0
    capital_gains_tax_percent: float = 0.0
    dividend_tax_percent: float = 0.0
    annual_inflation_percent: float = 0.0
    annual_risk_free_rate_percent: float = 0.0
    dividend_policy: DividendPolicy = "reinvest"
    cash_weight: float = 0.0


@dataclass(frozen=True)
class AdvancedSimulationResult:
    strategy: str
    start_date: date
    end_date: date
    initial_capital: float
    total_contributions: float
    final_value: float
    profit_loss: float
    return_value: float
    time_weighted_return: float
    cagr: float | None
    real_final_value: float
    real_time_weighted_return: float
    maximum_drawdown: float
    annualized_volatility: float | None
    sharpe_ratio: float | None
    sortino_ratio: float | None
    calmar_ratio: float | None
    downside_deviation: float | None
    value_at_risk_95: float | None
    expected_shortfall_95: float | None
    worst_month: str | None
    worst_month_return: float | None
    worst_year: str | None
    worst_year_return: float | None
    longest_drawdown_days: int
    maximum_drawdown_recovery_days: int | None
    fees_paid: float
    taxes_paid: float
    dividends_received: float
    rebalance_count: int
    portfolio_values: pd.Series
    real_portfolio_values: pd.Series
    cash_values: pd.Series
    position_values: pd.DataFrame
    correlation_matrix: pd.DataFrame
    positions: tuple[SimulationPosition, ...]


@dataclass(frozen=True)
class SimulationComparison:
    buy_and_hold: AdvancedSimulationResult
    rebalanced: AdvancedSimulationResult | None


@dataclass
class _Holding:
    shares: float = 0.0
    cost_basis: float = 0.0
    allocated: float = 0.0
    entry_date: date | None = None
    entry_price: float | None = None


def simulate_strategies(
    price_histories: dict[str, pd.Series],
    dividend_histories: dict[str, pd.Series],
    weights: dict[str, float],
    initial_capital: float,
    start_date: date,
    end_date: date,
    assumptions: SimulationAssumptions,
) -> SimulationComparison:
    _validate_inputs(weights, initial_capital, start_date, end_date, assumptions)
    buy_and_hold = _simulate(
        price_histories,
        dividend_histories,
        weights,
        initial_capital,
        start_date,
        end_date,
        assumptions,
        rebalance_frequency="none",
        strategy="Buy and hold",
    )
    rebalanced = None
    if assumptions.rebalance_frequency != "none":
        rebalanced = _simulate(
            price_histories,
            dividend_histories,
            weights,
            initial_capital,
            start_date,
            end_date,
            assumptions,
            rebalance_frequency=assumptions.rebalance_frequency,
            strategy=f"{assumptions.rebalance_frequency.title()} rebalancing",
        )
    return SimulationComparison(buy_and_hold=buy_and_hold, rebalanced=rebalanced)


def _simulate(
    price_histories: dict[str, pd.Series],
    dividend_histories: dict[str, pd.Series],
    weights: dict[str, float],
    initial_capital: float,
    start_date: date,
    end_date: date,
    assumptions: SimulationAssumptions,
    *,
    rebalance_frequency: Frequency,
    strategy: str,
) -> AdvancedSimulationResult:
    calendar = _calendar(start_date, end_date)
    tickers = list(weights)
    prices = _price_frame(price_histories, tickers, calendar, start_date, end_date)
    dividends = _dividend_frame(dividend_histories, tickers, calendar, start_date, end_date)
    holdings = {ticker: _Holding() for ticker in weights}
    pending = {ticker: initial_capital * weight for ticker, weight in weights.items()}
    for ticker, amount in pending.items():
        holdings[ticker].allocated += amount

    cash = initial_capital
    total_contributions = initial_capital
    fees_paid = 0.0
    taxes_paid = 0.0
    dividends_received = 0.0
    rebalance_count = 0
    portfolio_rows: list[float] = []
    cash_rows: list[float] = []
    position_rows: list[dict[str, float]] = []
    external_flows: list[float] = []
    previous_day: pd.Timestamp | None = None

    for current_day in calendar:
        flow = 0.0
        if previous_day is not None and _is_period_event(
            previous_day,
            current_day,
            start_date,
            assumptions.contribution_frequency,
        ):
            flow = assumptions.contribution_amount
            cash += flow
            total_contributions += flow
            for ticker, weight in weights.items():
                allocation = flow * weight
                pending[ticker] += allocation
                holdings[ticker].allocated += allocation

        for ticker, holding in holdings.items():
            dividend = float(dividends.at[current_day, ticker])
            if holding.shares <= 0 or dividend <= 0:
                continue
            gross = holding.shares * dividend
            tax = gross * assumptions.dividend_tax_percent / 100
            net = gross - tax
            dividends_received += gross
            taxes_paid += tax
            cash += net
            if assumptions.dividend_policy == "reinvest":
                pending[ticker] += net

        for ticker, budget in pending.items():
            price = prices.at[current_day, ticker]
            if budget <= 0 or pd.isna(price):
                continue
            spent, fee = _buy(holdings[ticker], float(price), min(budget, cash), assumptions, current_day)
            pending[ticker] -= spent
            cash -= spent
            fees_paid += fee

        if previous_day is not None and _is_period_event(
            previous_day,
            current_day,
            start_date,
            rebalance_frequency,
        ):
            cash, fees, taxes, traded = _rebalance(
                holdings,
                prices.loc[current_day],
                weights,
                cash,
                assumptions,
                current_day,
            )
            fees_paid += fees
            taxes_paid += taxes
            if traded:
                rebalance_count += 1

        values = {
            ticker: holding.shares * float(prices.at[current_day, ticker])
            if not pd.isna(prices.at[current_day, ticker])
            else 0.0
            for ticker, holding in holdings.items()
        }
        total = cash + sum(values.values())
        portfolio_rows.append(total)
        cash_rows.append(cash)
        position_rows.append(values)
        external_flows.append(initial_capital if previous_day is None else flow)
        previous_day = current_day

    portfolio = pd.Series(portfolio_rows, index=calendar, dtype=float, name=strategy)
    cash_values = pd.Series(cash_rows, index=calendar, dtype=float, name="Cash")
    position_values = pd.DataFrame(position_rows, index=calendar, columns=list(weights), dtype=float)
    daily_returns = _flow_adjusted_returns(portfolio, pd.Series(external_flows, index=calendar, dtype=float))
    performance_index = (1 + daily_returns).cumprod()
    time_weighted_return = float(performance_index.iloc[-1] - 1)
    elapsed_days = (end_date - start_date).days
    cagr = (
        (1 + time_weighted_return) ** (365.2425 / elapsed_days) - 1
        if time_weighted_return > -1 and elapsed_days > 0
        else None
    )
    inflation_factor = (1 + assumptions.annual_inflation_percent / 100) ** (
        (calendar - calendar[0]).days / 365.2425
    )
    real_portfolio = portfolio / inflation_factor
    real_twr = (1 + time_weighted_return) / float(inflation_factor[-1]) - 1
    drawdowns = performance_index.div(performance_index.cummax()).sub(1)
    volatility = float(daily_returns.std(ddof=1) * sqrt(252)) if len(daily_returns) >= 2 else None
    risk = _risk_statistics(
        daily_returns,
        performance_index,
        cagr,
        float(drawdowns.min()),
        assumptions.annual_risk_free_rate_percent,
    )
    correlation_matrix = _asset_correlation(prices, dividends)
    final_value = float(portfolio.iloc[-1])

    positions = tuple(
        _position_result(
            ticker,
            weights[ticker],
            holdings[ticker],
            price_histories.get(ticker),
            prices.iloc[-1].get(ticker),
            end_date,
        )
        for ticker in weights
    )
    return AdvancedSimulationResult(
        strategy=strategy,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        total_contributions=total_contributions,
        final_value=final_value,
        profit_loss=final_value - total_contributions,
        return_value=final_value / total_contributions - 1,
        time_weighted_return=time_weighted_return,
        cagr=cagr,
        real_final_value=float(real_portfolio.iloc[-1]),
        real_time_weighted_return=real_twr,
        maximum_drawdown=float(drawdowns.min()),
        annualized_volatility=volatility,
        sharpe_ratio=risk["sharpe_ratio"],
        sortino_ratio=risk["sortino_ratio"],
        calmar_ratio=risk["calmar_ratio"],
        downside_deviation=risk["downside_deviation"],
        value_at_risk_95=risk["value_at_risk_95"],
        expected_shortfall_95=risk["expected_shortfall_95"],
        worst_month=risk["worst_month"],
        worst_month_return=risk["worst_month_return"],
        worst_year=risk["worst_year"],
        worst_year_return=risk["worst_year_return"],
        longest_drawdown_days=risk["longest_drawdown_days"],
        maximum_drawdown_recovery_days=risk["maximum_drawdown_recovery_days"],
        fees_paid=fees_paid,
        taxes_paid=taxes_paid,
        dividends_received=dividends_received,
        rebalance_count=rebalance_count,
        portfolio_values=portfolio,
        real_portfolio_values=real_portfolio,
        cash_values=cash_values,
        position_values=position_values,
        correlation_matrix=correlation_matrix,
        positions=positions,
    )


def _validate_inputs(
    weights: dict[str, float],
    initial_capital: float,
    start_date: date,
    end_date: date,
    assumptions: SimulationAssumptions,
) -> None:
    if initial_capital <= 0:
        raise SimulationError("Initial capital must be positive.")
    if end_date <= start_date:
        raise SimulationError("Simulation end date must be after its start date.")
    if not weights:
        raise SimulationError("Select at least one ticker for the simulation.")
    numeric_values = [
        assumptions.contribution_amount,
        assumptions.commission_percent,
        assumptions.commission_fixed,
        assumptions.spread_percent,
        assumptions.capital_gains_tax_percent,
        assumptions.dividend_tax_percent,
        assumptions.annual_inflation_percent,
        assumptions.annual_risk_free_rate_percent,
        assumptions.cash_weight,
        *weights.values(),
    ]
    if any(value < 0 for value in numeric_values):
        raise SimulationError("Weights, contributions, costs, taxes, inflation, and cash cannot be negative.")
    if assumptions.cash_weight > 1:
        raise SimulationError("Cash allocation cannot exceed 100%.")
    if abs(sum(weights.values()) + assumptions.cash_weight - 1.0) > 0.0001:
        raise SimulationError("Ticker weights plus cash must add up to 100%.")
    if assumptions.contribution_frequency not in FREQUENCY_MONTHS:
        raise SimulationError("Unsupported contribution frequency.")
    if assumptions.rebalance_frequency not in FREQUENCY_MONTHS:
        raise SimulationError("Unsupported rebalancing frequency.")
    if assumptions.dividend_policy not in {"reinvest", "cash"}:
        raise SimulationError("Unsupported dividend policy.")


def _calendar(start_date: date, end_date: date) -> pd.DatetimeIndex:
    business_days = pd.date_range(start_date, end_date, freq="B")
    return business_days.union(pd.DatetimeIndex([pd.Timestamp(start_date), pd.Timestamp(end_date)])).sort_values()


def _price_frame(
    histories: dict[str, pd.Series],
    tickers: list[str],
    calendar: pd.DatetimeIndex,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    values = {}
    for ticker in tickers:
        prices = _normalize_prices(histories.get(ticker))
        if prices.empty:
            values[ticker] = pd.Series(index=calendar, dtype=float)
            continue
        prices = prices[(prices.index.date >= start_date) & (prices.index.date <= end_date)]
        values[ticker] = prices.reindex(calendar).ffill()
    return pd.DataFrame(values, index=calendar, dtype=float)


def _dividend_frame(
    histories: dict[str, pd.Series],
    tickers: list[str],
    calendar: pd.DatetimeIndex,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    values = {}
    for ticker in tickers:
        raw = histories.get(ticker)
        if raw is None or raw.empty:
            values[ticker] = pd.Series(0.0, index=calendar)
            continue
        series = pd.to_numeric(raw, errors="coerce").fillna(0)
        series.index = pd.to_datetime(series.index, errors="coerce", utc=True).tz_convert(None).normalize()
        series = series.groupby(level=0).sum()
        series = series[(series.index.date >= start_date) & (series.index.date <= end_date)]
        values[ticker] = series.reindex(calendar, fill_value=0)
    return pd.DataFrame(values, index=calendar, columns=tickers, dtype=float).fillna(0)


def _is_period_event(
    previous: pd.Timestamp,
    current: pd.Timestamp,
    start_date: date,
    frequency: Frequency,
) -> bool:
    months = FREQUENCY_MONTHS[frequency]
    if months is None or previous.month == current.month:
        return False
    elapsed = (current.year - start_date.year) * 12 + current.month - start_date.month
    return elapsed > 0 and elapsed % months == 0


def _transaction_rate(assumptions: SimulationAssumptions) -> float:
    return (assumptions.commission_percent + assumptions.spread_percent) / 100


def _buy(
    holding: _Holding,
    price: float,
    budget: float,
    assumptions: SimulationAssumptions,
    current_day: pd.Timestamp,
) -> tuple[float, float]:
    fixed = assumptions.commission_fixed
    if budget <= fixed or price <= 0:
        return 0.0, 0.0
    rate = _transaction_rate(assumptions)
    notional = (budget - fixed) / (1 + rate)
    fee = fixed + notional * rate
    spent = notional + fee
    holding.shares += notional / price
    holding.cost_basis += notional + fee
    if holding.entry_date is None:
        holding.entry_date = current_day.date()
        holding.entry_price = price
    return spent, fee


def _sell(
    holding: _Holding,
    price: float,
    notional: float,
    assumptions: SimulationAssumptions,
) -> tuple[float, float, float]:
    if notional <= 0 or holding.shares <= 0:
        return 0.0, 0.0, 0.0
    gross_value = holding.shares * price
    notional = min(notional, gross_value)
    fraction = notional / gross_value
    basis = holding.cost_basis * fraction
    shares_sold = holding.shares * fraction
    fee = min(notional, assumptions.commission_fixed + notional * _transaction_rate(assumptions))
    gain = max(0.0, notional - fee - basis)
    tax = gain * assumptions.capital_gains_tax_percent / 100
    holding.shares -= shares_sold
    holding.cost_basis -= basis
    return notional - fee - tax, fee, tax


def _rebalance(
    holdings: dict[str, _Holding],
    prices: pd.Series,
    weights: dict[str, float],
    cash: float,
    assumptions: SimulationAssumptions,
    current_day: pd.Timestamp,
) -> tuple[float, float, float, bool]:
    values = {
        ticker: holding.shares * float(prices[ticker]) if not pd.isna(prices[ticker]) else 0.0
        for ticker, holding in holdings.items()
    }
    total = cash + sum(values.values())
    fees = taxes = 0.0
    traded = False
    for ticker, current_value in values.items():
        target = total * weights[ticker]
        if current_value <= target or pd.isna(prices[ticker]):
            continue
        proceeds, fee, tax = _sell(
            holdings[ticker],
            float(prices[ticker]),
            current_value - target,
            assumptions,
        )
        cash += proceeds
        fees += fee
        taxes += tax
        traded = traded or proceeds > 0

    total_after_sales = cash + sum(
        holding.shares * float(prices[ticker]) if not pd.isna(prices[ticker]) else 0.0
        for ticker, holding in holdings.items()
    )
    cash_reserve = total_after_sales * assumptions.cash_weight
    for ticker, holding in holdings.items():
        if pd.isna(prices[ticker]):
            continue
        current_value = holding.shares * float(prices[ticker])
        budget = min(max(0.0, total_after_sales * weights[ticker] - current_value), max(0.0, cash - cash_reserve))
        spent, fee = _buy(holding, float(prices[ticker]), budget, assumptions, current_day)
        cash -= spent
        fees += fee
        traded = traded or spent > 0
    return cash, fees, taxes, traded


def _flow_adjusted_returns(portfolio: pd.Series, flows: pd.Series) -> pd.Series:
    previous = portfolio.shift(1)
    returns = (portfolio - flows).div(previous).sub(1)
    returns.iloc[0] = portfolio.iloc[0] / float(flows.iloc[0] or portfolio.iloc[0]) - 1
    return returns.fillna(0)


def _risk_statistics(
    daily_returns: pd.Series,
    performance_index: pd.Series,
    cagr: float | None,
    maximum_drawdown: float,
    annual_risk_free_rate_percent: float,
) -> dict[str, Any]:
    usable = pd.to_numeric(daily_returns, errors="coerce").dropna()
    risk_free_daily = (1 + annual_risk_free_rate_percent / 100) ** (1 / 252) - 1
    excess = usable - risk_free_daily
    standard_deviation = float(usable.std(ddof=1)) if len(usable) >= 2 else None
    downside = excess.clip(upper=0)
    downside_daily_value = float((downside.pow(2).mean()) ** 0.5) if not downside.empty else 0.0
    downside_daily = downside_daily_value if downside_daily_value > 0 else None
    sharpe = (
        float(excess.mean() / standard_deviation * sqrt(252))
        if standard_deviation is not None and standard_deviation > 0
        else None
    )
    sortino = (
        float(excess.mean() / downside_daily * sqrt(252))
        if downside_daily is not None and downside_daily > 0
        else None
    )
    calmar = cagr / abs(maximum_drawdown) if cagr is not None and maximum_drawdown < 0 else None
    quantile = float(usable.quantile(0.05)) if len(usable) >= 2 else None
    tail_count = max(1, ceil(len(usable) * 0.05))
    tail = usable.nsmallest(tail_count) if quantile is not None else pd.Series(dtype=float)
    value_at_risk = max(0.0, -quantile) if quantile is not None else None
    expected_shortfall = max(0.0, -float(tail.mean())) if not tail.empty else None
    monthly = _period_returns(performance_index, "ME")
    yearly = _period_returns(performance_index, "YE")
    worst_month_at, worst_month_return = _worst_period(monthly, "%Y-%m")
    worst_year_at, worst_year_return = _worst_period(yearly, "%Y")
    longest_drawdown, recovery = _drawdown_durations(performance_index)
    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "downside_deviation": downside_daily * sqrt(252) if downside_daily is not None else None,
        "value_at_risk_95": value_at_risk,
        "expected_shortfall_95": expected_shortfall,
        "worst_month": worst_month_at,
        "worst_month_return": worst_month_return,
        "worst_year": worst_year_at,
        "worst_year_return": worst_year_return,
        "longest_drawdown_days": longest_drawdown,
        "maximum_drawdown_recovery_days": recovery,
    }


def _period_returns(performance_index: pd.Series, frequency: str) -> pd.Series:
    if performance_index.empty:
        return pd.Series(dtype=float)
    period_end = performance_index.resample(frequency).last().dropna()
    returns = period_end.pct_change()
    if not returns.empty:
        returns.iloc[0] = period_end.iloc[0] - 1
    return returns.dropna()


def _worst_period(returns: pd.Series, date_format: str) -> tuple[str | None, float | None]:
    if returns.empty:
        return None, None
    timestamp = returns.idxmin()
    return timestamp.strftime(date_format), float(returns.loc[timestamp])


def _drawdown_durations(performance_index: pd.Series) -> tuple[int, int | None]:
    if performance_index.empty:
        return 0, None
    running_peak = performance_index.cummax()
    underwater = performance_index < running_peak
    longest = 0
    peak_date = performance_index.index[0]
    in_drawdown = False
    for timestamp, is_underwater in underwater.items():
        if is_underwater:
            in_drawdown = True
        else:
            if in_drawdown:
                longest = max(longest, (timestamp - peak_date).days)
                in_drawdown = False
            peak_date = timestamp
    if in_drawdown:
        longest = max(longest, (performance_index.index[-1] - peak_date).days)

    drawdowns = performance_index.div(running_peak).sub(1)
    trough = drawdowns.idxmin()
    peak_value = float(running_peak.loc[trough])
    recovered = performance_index.loc[trough:]
    recovered = recovered[recovered >= peak_value]
    recovery_days = (recovered.index[0] - trough).days if not recovered.empty else None
    return longest, recovery_days


def _asset_correlation(prices: pd.DataFrame, dividends: pd.DataFrame) -> pd.DataFrame:
    previous_prices = prices.shift(1)
    total_returns = prices.add(dividends, fill_value=0).div(previous_prices).sub(1)
    total_returns = total_returns.replace([float("inf"), float("-inf")], pd.NA)
    usable = total_returns.dropna(how="all")
    return usable.corr(min_periods=2)


def _position_result(
    ticker: str,
    weight: float,
    holding: _Holding,
    history: pd.Series | None,
    final_price_value: float | None,
    end_date: date,
) -> SimulationPosition:
    final_price = None if final_price_value is None or pd.isna(final_price_value) else float(final_price_value)
    final_value = holding.shares * final_price if final_price is not None else 0.0
    return SimulationPosition(
        ticker=ticker,
        weight=weight,
        allocation=holding.allocated,
        entry_date=holding.entry_date,
        entry_price=holding.entry_price,
        shares=holding.shares,
        final_price=final_price,
        final_value=final_value,
        profit_loss=final_value - holding.allocated,
        return_value=final_value / holding.allocated - 1 if holding.allocated > 0 else 0.0,
        trailing_returns=calculate_trailing_returns(history, end_date),
        status="Invested" if holding.entry_date else "Cash: no usable prices",
    )
