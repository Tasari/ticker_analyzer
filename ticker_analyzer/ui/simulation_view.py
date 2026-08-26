from __future__ import annotations

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.portfolio.returns import (
    ACCOUNT_RETURNS_STATE_KEY,
    ACCOUNT_STATEMENT_TICKER,
    ReturnsTable,
    ReturnsTableError,
    analyze_returns_range,
)
from ticker_analyzer.portfolio.simulation import (
    TRAILING_RETURN_PERIODS,
    SimulationError,
    SimulationResult,
    simulate_buy_and_hold,
)
from ticker_analyzer.providers.market_data import retry_transient

BASE_CURRENCIES = ("USD", "EUR", "PLN")
MAX_SIMULATION_WORKERS = 5


def render_simulation(results: dict[str, dict]) -> None:
    st.subheader("Portfolio Simulation")
    st.caption(
        "Buy-and-hold simulation using adjusted Yahoo prices, fractional shares, and no fees or taxes. "
        "This is a historical illustration, not investment advice."
    )
    account_returns = st.session_state.get(ACCOUNT_RETURNS_STATE_KEY)
    account_selected = ACCOUNT_STATEMENT_TICKER in st.session_state.get("selected_tickers", [])
    if not isinstance(account_returns, ReturnsTable) or not account_selected:
        account_returns = None
    tickers = list(results)
    if account_returns is not None:
        tickers.append(ACCOUNT_STATEMENT_TICKER)
    if not tickers:
        st.info("Analyze at least one ticker or import an Account Statement returns table first.")
        return

    today = date.today()
    default_start = today - timedelta(days=365)
    default_end = today
    if account_returns is not None:
        default_start = account_returns.first_month
        last_month = account_returns.last_month
        default_end = min(
            today,
            date(last_month.year, last_month.month, monthrange(last_month.year, last_month.month)[1]),
        )
        st.info(
            f"{ACCOUNT_STATEMENT_TICKER} represents the imported monthly Account Statement returns "
            "and is included as a simulation asset."
        )
    controls = st.columns(4)
    initial_capital = float(
        controls[0].number_input(
            "Initial capital",
            min_value=1.0,
            value=10_000.0,
            step=1_000.0,
            key="simulation_initial_capital",
        )
    )
    start_date = controls[1].date_input(
        "Simulation start",
        value=default_start,
        max_value=today,
        key="simulation_start_date",
    )
    end_date = controls[2].date_input(
        "Simulation end",
        value=default_end,
        max_value=today,
        key="simulation_end_date",
    )
    base_currency = controls[3].selectbox(
        "Base currency",
        BASE_CURRENCIES,
        key="simulation_base_currency",
    )

    equal_weights = st.checkbox("Equal weights", value=True, key="simulation_equal_weights")
    if equal_weights:
        weights = {ticker: 1 / len(tickers) for ticker in tickers}
        st.caption(f"Each of {len(tickers)} ticker(s) receives {100 / len(tickers):.2f}% of the capital.")
    else:
        st.caption("Set allocation weights; their sum must equal 100%.")
        columns = st.columns(min(4, len(tickers)))
        default_weight = 100 / len(tickers)
        weights = {
            ticker: float(
                columns[index % len(columns)].number_input(
                    f"{ticker} weight (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_weight),
                    step=1.0,
                    key=f"simulation_weight_{ticker}",
                )
            )
            / 100
            for index, ticker in enumerate(tickers)
        }
        st.caption(f"Current total: {sum(weights.values()):.2%}")
    show_positions = st.checkbox(
        "Show individual ticker lines",
        value=True,
        key="simulation_show_positions",
    )

    signature = (tuple(tickers), initial_capital, start_date, end_date, base_currency, tuple(weights.items()))
    if st.button("Run simulation", type="primary", key="run_simulation"):
        try:
            with st.spinner("Fetching adjusted prices and exchange rates..."):
                histories, warnings = _fetch_simulation_histories(
                    results,
                    start_date,
                    end_date,
                    base_currency,
                    account_returns=account_returns,
                )
            simulation = simulate_buy_and_hold(
                histories,
                weights,
                initial_capital,
                start_date,
                end_date,
            )
        except SimulationError as exc:
            st.error(str(exc))
        else:
            st.session_state["simulation_output"] = {
                "signature": signature,
                "result": simulation,
                "warnings": warnings,
                "currency": base_currency,
            }

    output = st.session_state.get("simulation_output")
    if not output or output.get("signature") != signature:
        st.info("Choose the range and allocation, then run the simulation.")
        return
    for warning in output.get("warnings", []):
        st.warning(warning)
    _render_simulation_result(
        output["result"],
        output["currency"],
        show_positions=show_positions,
    )


def _fetch_simulation_histories(
    results: dict[str, dict],
    start_date: date,
    end_date: date,
    base_currency: str,
    *,
    account_returns: ReturnsTable | None = None,
) -> tuple[dict[str, pd.Series], list[str]]:
    histories: dict[str, pd.Series] = {}
    warnings: list[str] = []
    history_start = _simulation_history_start(start_date, end_date)

    def fetch_one(ticker: str) -> tuple[str, pd.Series, str | None]:
        try:
            if ticker == ACCOUNT_STATEMENT_TICKER:
                if account_returns is None:
                    raise ReturnsTableError("the imported returns table is no longer available.")
                return ticker, _account_statement_prices(account_returns, history_start, end_date), None
            prices = _cached_adjusted_prices(ticker, history_start, end_date)
            currency = str(results[ticker].get("currency") or base_currency)
            converted = _convert_to_base_currency(
                prices,
                currency,
                base_currency,
                history_start,
                end_date,
            )
            return ticker, converted, None
        except (RuntimeError, ValueError) as exc:
            return ticker, pd.Series(dtype=float), str(exc)

    tickers = [*results]
    if account_returns is not None:
        tickers.append(ACCOUNT_STATEMENT_TICKER)
    with ThreadPoolExecutor(max_workers=min(MAX_SIMULATION_WORKERS, len(tickers))) as executor:
        futures = [executor.submit(fetch_one, ticker) for ticker in tickers]
        for future in as_completed(futures):
            ticker, prices, error = future.result()
            histories[ticker] = prices
            if error:
                warnings.append(f"{ticker}: {error} Its allocation remains cash.")
    return {ticker: histories.get(ticker, pd.Series(dtype=float)) for ticker in tickers}, warnings


def _simulation_history_start(start_date: date, end_date: date) -> date:
    longest_period_months = max(months for _, months in TRAILING_RETURN_PERIODS)
    boundary = pd.Timestamp(end_date) - pd.DateOffset(months=longest_period_months)
    return min(start_date, boundary.date() - timedelta(days=7))


def _account_statement_prices(
    returns_table: ReturnsTable,
    start_date: date,
    end_date: date,
) -> pd.Series:
    available_start = max(start_date, returns_table.first_month)
    last_month = returns_table.last_month
    available_end = min(
        end_date,
        date(last_month.year, last_month.month, monthrange(last_month.year, last_month.month)[1]),
    )
    if available_end < available_start:
        raise ReturnsTableError("the imported returns table does not overlap the requested history.")
    analysis = analyze_returns_range(
        returns_table,
        available_start,
        available_end,
        initial_capital=100.0,
    )
    return pd.Series(
        [point.value for point in analysis.growth],
        index=pd.to_datetime([point.day for point in analysis.growth]),
        dtype=float,
    )


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def _cached_adjusted_prices(ticker: str, start_date: date, end_date: date) -> pd.Series:
    import yfinance as yf

    try:
        history = retry_transient(
            lambda: yf.Ticker(ticker).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=True,
                actions=False,
            )
        )
    except Exception as exc:
        raise RuntimeError(f"price data could not be downloaded: {exc}") from exc
    if history.empty or "Close" not in history:
        raise RuntimeError("no adjusted prices are available for this range.")
    return pd.to_numeric(history["Close"], errors="coerce").dropna()


def _convert_to_base_currency(
    prices: pd.Series,
    source_currency: str,
    base_currency: str,
    start_date: date,
    end_date: date,
) -> pd.Series:
    raw_currency = source_currency.strip()
    source = raw_currency.upper()
    converted = _daily_series(prices)
    if raw_currency in {"GBp", "GBX"}:
        converted = converted / 100
        source = "GBP"
    if not source or source == base_currency:
        return converted
    factor = _daily_series(_cached_fx_factor(source, base_currency, start_date, end_date))
    aligned_factor = factor.reindex(converted.index, method="ffill").bfill()
    if aligned_factor.isna().any():
        raise RuntimeError(f"{source}/{base_currency} exchange-rate history is incomplete.")
    return converted * aligned_factor


def _daily_series(values: pd.Series) -> pd.Series:
    normalized = pd.to_numeric(values, errors="coerce").dropna()
    normalized.index = pd.to_datetime(normalized.index, errors="coerce", utc=True).tz_convert(None).normalize()
    return normalized[~normalized.index.duplicated(keep="last")].sort_index()


@st.cache_data(ttl=3600, max_entries=24, show_spinner=False)
def _cached_fx_factor(source: str, target: str, start_date: date, end_date: date) -> pd.Series:
    direct = _try_fx_history(f"{source}{target}=X", start_date, end_date)
    if not direct.empty:
        return direct
    inverse = _try_fx_history(f"{target}{source}=X", start_date, end_date)
    if inverse.empty or (inverse <= 0).any():
        raise RuntimeError(f"no {source}/{target} exchange-rate history is available.")
    return 1 / inverse


def _try_fx_history(symbol: str, start_date: date, end_date: date) -> pd.Series:
    try:
        return _cached_adjusted_prices(symbol, start_date - timedelta(days=7), end_date)
    except RuntimeError:
        return pd.Series(dtype=float)


def _render_simulation_result(
    result: SimulationResult,
    currency: str,
    *,
    show_positions: bool = True,
) -> None:
    metrics = st.columns(6)
    metrics[0].metric("Initial capital", _money(result.initial_capital, currency))
    metrics[1].metric("Final value", _money(result.final_value, currency))
    metrics[2].metric("P/L", _money(result.profit_loss, currency))
    metrics[3].metric("ROI", _percent(result.return_value))
    metrics[4].metric("CAGR", _percent(result.cagr))
    metrics[5].metric("Max drawdown", _percent(result.maximum_drawdown))
    st.caption(f"Annualized volatility: {_percent(result.annualized_volatility)}")

    chart = result.position_values.copy() if show_positions else pd.DataFrame(index=result.portfolio_values.index)
    chart.insert(0, "Portfolio", result.portfolio_values)
    chart.index.name = "Date"
    figure = px.line(
        chart.reset_index().melt(id_vars="Date", var_name="Series", value_name="Value"),
        x="Date",
        y="Value",
        color="Series",
        title="Buy-and-hold portfolio value",
    )
    figure.update_layout(yaxis_title=f"Value ({currency})", xaxis_title=None)
    st.plotly_chart(figure, width="stretch")

    frame = pd.DataFrame(
        [
            {
                "Ticker": position.ticker,
                "Weight": position.weight * 100,
                "Allocation": position.allocation,
                "Entry date": position.entry_date,
                "Entry price": position.entry_price,
                "Shares": position.shares,
                "Final price": position.final_price,
                "Final value": position.final_value,
                "P/L": position.profit_loss,
                "Selected range return": position.return_value * 100,
                **{
                    label: value * 100 if value is not None else None
                    for label, value in position.trailing_returns
                },
                "Portfolio return contribution": position.profit_loss / result.initial_capital * 100,
                "Share of total P/L": (
                    position.profit_loss / result.profit_loss * 100
                    if abs(result.profit_loss) >= 0.005
                    else None
                ),
                "Status": position.status,
            }
            for position in result.positions
        ]
    )
    st.caption(
        "Rolling returns are measured backward from the selected end date and are independent of the chart range. "
        "N/A means the ticker has no price at or before the required boundary."
    )
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Weight": st.column_config.NumberColumn(format="%.2f%%"),
            "Allocation": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Entry price": st.column_config.NumberColumn(format="%.4f"),
            "Shares": st.column_config.NumberColumn(format="%.6f"),
            "Final price": st.column_config.NumberColumn(format="%.4f"),
            "Final value": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "P/L": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Selected range return": st.column_config.NumberColumn(format="%.2f%%"),
            **{
                label: st.column_config.NumberColumn(format="%.2f%%")
                for label, _ in TRAILING_RETURN_PERIODS
            },
            "Portfolio return contribution": st.column_config.NumberColumn(format="%.2f pp"),
            "Share of total P/L": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"
