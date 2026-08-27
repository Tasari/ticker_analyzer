from __future__ import annotations

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.portfolio.advanced_simulation import (
    SimulationAssumptions,
    SimulationComparison,
    simulate_strategies,
)
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
)
from ticker_analyzer.providers.market_data import retry_transient

BASE_CURRENCIES = ("USD", "EUR", "PLN")
MAX_SIMULATION_WORKERS = 5


def render_simulation(results: dict[str, dict]) -> None:
    st.subheader("Portfolio Simulation")
    st.caption(
        "Historical portfolio simulation with fractional shares, cash flows, rebalancing, dividends and configurable "
        "friction. Taxes and execution costs are estimates, not a broker or tax settlement. This is not investment advice."
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
    controls = st.columns(5)
    initial_capital = float(
        controls[0].number_input(
            "Initial capital",
            min_value=1.0,
            value=10_000.0,
            step=1_000.0,
            key="simulation_initial_capital",
        )
    )
    contribution_amount = float(
        controls[1].number_input(
            "Periodic contribution",
            min_value=0.0,
            value=0.0,
            step=100.0,
            key="simulation_contribution_amount",
        )
    )
    start_date = controls[2].date_input(
        "Simulation start",
        value=default_start,
        max_value=today,
        key="simulation_start_date",
    )
    end_date = controls[3].date_input(
        "Simulation end",
        value=default_end,
        max_value=today,
        key="simulation_end_date",
    )
    base_currency = controls[4].selectbox(
        "Base currency",
        BASE_CURRENCIES,
        key="simulation_base_currency",
    )

    assumptions = _assumption_controls(contribution_amount)
    investable_weight = 1 - assumptions.cash_weight
    equal_weights = st.checkbox("Equal ticker weights", value=True, key="simulation_equal_weights")
    if equal_weights:
        weights = {ticker: investable_weight / len(tickers) for ticker in tickers}
        st.caption(
            f"Each of {len(tickers)} ticker(s) receives {investable_weight * 100 / len(tickers):.2f}%; "
            f"cash receives {assumptions.cash_weight:.2%}."
        )
    else:
        st.caption("Set allocation weights; their sum must equal 100%.")
        columns = st.columns(min(4, len(tickers)))
        default_weight = investable_weight * 100 / len(tickers)
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
        st.caption(f"Tickers: {sum(weights.values()):.2%}; cash: {assumptions.cash_weight:.2%}; total: {sum(weights.values()) + assumptions.cash_weight:.2%}")
    show_positions = st.checkbox(
        "Show individual ticker lines",
        value=True,
        key="simulation_show_positions",
    )

    signature = (
        tuple(tickers), initial_capital, start_date, end_date, base_currency, tuple(weights.items()), assumptions
    )
    if st.button("Run simulation", type="primary", key="run_simulation"):
        try:
            with st.spinner("Fetching prices, dividends and exchange rates..."):
                histories, dividends, warnings = _fetch_simulation_market_data(
                    results,
                    start_date,
                    end_date,
                    base_currency,
                    account_returns=account_returns,
                )
            simulation = simulate_strategies(
                histories,
                dividends,
                weights,
                initial_capital,
                start_date,
                end_date,
                assumptions,
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
    _render_simulation_comparison(
        output["result"],
        output["currency"],
        show_positions=show_positions,
    )


def _assumption_controls(contribution_amount: float) -> SimulationAssumptions:
    frequency_labels = {
        "none": "None",
        "monthly": "Monthly",
        "quarterly": "Quarterly",
        "annual": "Annual",
    }
    with st.expander("Contributions, rebalancing and assumptions", expanded=True):
        strategy = st.columns(4)
        contribution_frequency = strategy[0].selectbox(
            "Contribution frequency",
            tuple(frequency_labels),
            index=1,
            format_func=frequency_labels.get,
            key="simulation_contribution_frequency",
            disabled=contribution_amount <= 0,
        )
        rebalance_frequency = strategy[1].selectbox(
            "Rebalancing",
            tuple(frequency_labels),
            format_func=frequency_labels.get,
            key="simulation_rebalance_frequency",
            help="When enabled, both buy-and-hold and the selected rebalanced strategy are calculated.",
        )
        dividend_policy = strategy[2].selectbox(
            "Dividends",
            ("reinvest", "cash"),
            format_func=lambda value: "Reinvest" if value == "reinvest" else "Keep as cash",
            key="simulation_dividend_policy",
        )
        cash_weight = float(
            strategy[3].number_input(
                "Target cash (%)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                step=1.0,
                key="simulation_cash_weight",
            )
        ) / 100

        costs = st.columns(6)
        commission_percent = _percent_input(costs[0], "Commission (%)", "simulation_commission_percent")
        commission_fixed = float(
            costs[1].number_input(
                "Fixed fee / order",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key="simulation_commission_fixed",
            )
        )
        spread_percent = _percent_input(costs[2], "Spread / trade (%)", "simulation_spread_percent")
        capital_gains_tax = _percent_input(costs[3], "Capital gains tax (%)", "simulation_gains_tax")
        dividend_tax = _percent_input(costs[4], "Dividend tax (%)", "simulation_dividend_tax")
        inflation = _percent_input(costs[5], "Inflation p.a. (%)", "simulation_inflation")
        st.caption(
            "Tax is charged on estimated positive realized gains and dividends. No tax-lot optimization, allowances, "
            "loss carry-forward or broker-specific rounding is modeled."
        )
    return SimulationAssumptions(
        contribution_amount=contribution_amount,
        contribution_frequency=contribution_frequency,
        rebalance_frequency=rebalance_frequency,
        commission_percent=commission_percent,
        commission_fixed=commission_fixed,
        spread_percent=spread_percent,
        capital_gains_tax_percent=capital_gains_tax,
        dividend_tax_percent=dividend_tax,
        annual_inflation_percent=inflation,
        dividend_policy=dividend_policy,
        cash_weight=cash_weight,
    )


def _percent_input(column, label: str, key: str) -> float:
    return float(
        column.number_input(
            label,
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.1,
            key=key,
        )
    )


def _fetch_simulation_market_data(
    results: dict[str, dict],
    start_date: date,
    end_date: date,
    base_currency: str,
    *,
    account_returns: ReturnsTable | None = None,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series], list[str]]:
    prices_by_ticker: dict[str, pd.Series] = {}
    dividends_by_ticker: dict[str, pd.Series] = {}
    warnings: list[str] = []
    history_start = _simulation_history_start(start_date, end_date)

    def fetch_one(ticker: str) -> tuple[str, pd.Series, pd.Series, str | None]:
        try:
            if ticker == ACCOUNT_STATEMENT_TICKER:
                if account_returns is None:
                    raise ReturnsTableError("the imported returns table is no longer available.")
                prices = _account_statement_prices(account_returns, history_start, end_date)
                return ticker, prices, pd.Series(dtype=float), None
            prices, dividends = _cached_market_history(ticker, history_start, end_date)
            currency = str(results[ticker].get("currency") or base_currency)
            return (
                ticker,
                _convert_to_base_currency(prices, currency, base_currency, history_start, end_date),
                _convert_to_base_currency(dividends, currency, base_currency, history_start, end_date),
                None,
            )
        except (RuntimeError, ValueError) as exc:
            return ticker, pd.Series(dtype=float), pd.Series(dtype=float), str(exc)

    tickers = [*results]
    if account_returns is not None:
        tickers.append(ACCOUNT_STATEMENT_TICKER)
        warnings.append(
            f"{ACCOUNT_STATEMENT_TICKER} is already a total-return series, so its dividends cannot be separated "
            "into reinvested and cash components."
        )
    with ThreadPoolExecutor(max_workers=min(MAX_SIMULATION_WORKERS, len(tickers))) as executor:
        futures = [executor.submit(fetch_one, ticker) for ticker in tickers]
        for future in as_completed(futures):
            ticker, prices, dividends, error = future.result()
            prices_by_ticker[ticker] = prices
            dividends_by_ticker[ticker] = dividends
            if error:
                warnings.append(f"{ticker}: {error} Its allocation remains cash.")
    return (
        {ticker: prices_by_ticker.get(ticker, pd.Series(dtype=float)) for ticker in tickers},
        {ticker: dividends_by_ticker.get(ticker, pd.Series(dtype=float)) for ticker in tickers},
        warnings,
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


@st.cache_data(ttl=3600, max_entries=64, show_spinner=False)
def _cached_market_history(
    ticker: str,
    start_date: date,
    end_date: date,
) -> tuple[pd.Series, pd.Series]:
    import yfinance as yf

    try:
        history = retry_transient(
            lambda: yf.Ticker(ticker).history(
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
            )
        )
    except Exception as exc:
        raise RuntimeError(f"market data could not be downloaded: {exc}") from exc
    if history.empty or "Close" not in history:
        raise RuntimeError("no prices are available for this range.")
    prices = pd.to_numeric(history["Close"], errors="coerce").dropna()
    dividends = (
        pd.to_numeric(history["Dividends"], errors="coerce").fillna(0)
        if "Dividends" in history
        else pd.Series(0.0, index=history.index)
    )
    return prices, dividends[dividends > 0]


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


def _render_simulation_comparison(
    comparison: SimulationComparison,
    currency: str,
    *,
    show_positions: bool = True,
) -> None:
    results = [comparison.buy_and_hold]
    if comparison.rebalanced is not None:
        results.append(comparison.rebalanced)
    comparison_frame = pd.DataFrame(
        [
            {
                "Strategy": result.strategy,
                "Contributed": result.total_contributions,
                "Final value": result.final_value,
                "P/L": result.profit_loss,
                "Simple ROI": result.return_value * 100,
                "TWR": result.time_weighted_return * 100,
                "CAGR (from TWR)": result.cagr * 100 if result.cagr is not None else None,
                "Real final value": result.real_final_value,
                "Real TWR": result.real_time_weighted_return * 100,
                "Max drawdown": result.maximum_drawdown * 100,
                "Volatility": result.annualized_volatility * 100 if result.annualized_volatility is not None else None,
                "Fees": result.fees_paid,
                "Taxes": result.taxes_paid,
                "Gross dividends": result.dividends_received,
                "Rebalances": result.rebalance_count,
            }
            for result in results
        ]
    )
    st.dataframe(
        comparison_frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Contributed": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Final value": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "P/L": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Simple ROI": st.column_config.NumberColumn(format="%.2f%%"),
            "TWR": st.column_config.NumberColumn(format="%.2f%%"),
            "CAGR (from TWR)": st.column_config.NumberColumn(format="%.2f%%"),
            "Real final value": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Real TWR": st.column_config.NumberColumn(format="%.2f%%"),
            "Max drawdown": st.column_config.NumberColumn(format="%.2f%%"),
            "Volatility": st.column_config.NumberColumn(format="%.2f%%"),
            "Fees": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Taxes": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Gross dividends": st.column_config.NumberColumn(format=f"%.2f {currency}"),
        },
    )

    selected_name = st.selectbox(
        "Detailed strategy",
        [result.strategy for result in results],
        key="simulation_detailed_strategy",
    )
    result = next(item for item in results if item.strategy == selected_name)
    metrics = st.columns(6)
    metrics[0].metric("Total contributed", _money(result.total_contributions, currency))
    metrics[1].metric("Final value", _money(result.final_value, currency))
    metrics[2].metric("P/L", _money(result.profit_loss, currency))
    metrics[3].metric("TWR", _percent(result.time_weighted_return))
    metrics[4].metric("Real TWR", _percent(result.real_time_weighted_return))
    metrics[5].metric("Max drawdown", _percent(result.maximum_drawdown))
    st.caption(
        f"CAGR from TWR: {_percent(result.cagr)} | Annualized volatility: {_percent(result.annualized_volatility)} | "
        f"Fees: {_money(result.fees_paid, currency)} | Taxes: {_money(result.taxes_paid, currency)} | "
        f"Gross dividends: {_money(result.dividends_received, currency)}"
    )
    st.caption("Real values and Real TWR are expressed in purchasing power from the simulation start date.")

    chart = pd.DataFrame({item.strategy: item.portfolio_values for item in results})
    if st.checkbox("Show inflation-adjusted strategy lines", value=False, key="simulation_show_real"):
        for item in results:
            chart[f"{item.strategy} (real)"] = item.real_portfolio_values
    if show_positions:
        for ticker in result.position_values:
            chart[f"{selected_name}: {ticker}"] = result.position_values[ticker]
        chart[f"{selected_name}: Cash"] = result.cash_values
    chart.index.name = "Date"
    figure = px.line(
        chart.reset_index().melt(id_vars="Date", var_name="Series", value_name="Value"),
        x="Date",
        y="Value",
        color="Series",
        title="Portfolio strategy comparison",
    )
    figure.update_layout(yaxis_title=f"Value ({currency})", xaxis_title=None)
    st.plotly_chart(figure, width="stretch")

    frame = pd.DataFrame(
        [
            {
                "Ticker": position.ticker,
                "Target weight": position.weight * 100,
                "Directed contributions": position.allocation,
                "Entry date": position.entry_date,
                "Entry price": position.entry_price,
                "Shares": position.shares,
                "Final price": position.final_price,
                "Position value": position.final_value,
                "Current portfolio weight": position.final_value / result.final_value * 100,
                **{
                    label: value * 100 if value is not None else None
                    for label, value in position.trailing_returns
                },
                "Status": position.status,
            }
            for position in result.positions
        ]
    )
    st.caption(
        "Rolling returns are measured backward from the selected end date and are independent of the chart range. "
        "Cash is shown on the chart and is not included as a ticker row below."
    )
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Target weight": st.column_config.NumberColumn(format="%.2f%%"),
            "Directed contributions": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Entry price": st.column_config.NumberColumn(format="%.4f"),
            "Shares": st.column_config.NumberColumn(format="%.6f"),
            "Final price": st.column_config.NumberColumn(format="%.4f"),
            "Position value": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Current portfolio weight": st.column_config.NumberColumn(format="%.2f%%"),
            **{
                label: st.column_config.NumberColumn(format="%.2f%%")
                for label, _ in TRAILING_RETURN_PERIODS
            },
        },
    )


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"
