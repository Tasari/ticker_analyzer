from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import streamlit as st

from ticker_analyzer.portfolio.advanced_simulation import (
    SimulationAssumptions,
    simulate_strategies,
)
from ticker_analyzer.portfolio.returns import (
    ACCOUNT_RETURNS_STATE_KEY,
    ACCOUNT_STATEMENT_TICKER,
    ReturnsTable,
)
from ticker_analyzer.portfolio.simulation import (
    SimulationError,
)
from ticker_analyzer.providers.simulation_data import MAX_SIMULATION_WORKERS as MAX_SIMULATION_WORKERS
from ticker_analyzer.providers.simulation_data import (
    _account_statement_prices as _account_statement_prices,
)
from ticker_analyzer.providers.simulation_data import (
    _cached_adjusted_prices as _cached_adjusted_prices,
)
from ticker_analyzer.providers.simulation_data import (
    _cached_fx_factor as _cached_fx_factor,
)
from ticker_analyzer.providers.simulation_data import (
    _cached_market_history as _cached_market_history,
)
from ticker_analyzer.providers.simulation_data import (
    _convert_to_base_currency as _convert_to_base_currency,
)
from ticker_analyzer.providers.simulation_data import (
    _daily_series as _daily_series,
)
from ticker_analyzer.providers.simulation_data import (
    _fetch_simulation_histories as _fetch_simulation_histories,
)
from ticker_analyzer.providers.simulation_data import (
    _fetch_simulation_market_data as _fetch_simulation_market_data,
)
from ticker_analyzer.providers.simulation_data import (
    _simulation_history_start as _simulation_history_start,
)
from ticker_analyzer.providers.simulation_data import (
    _try_fx_history as _try_fx_history,
)
from ticker_analyzer.ui.simulation_results_view import (
    _money as _money,
)
from ticker_analyzer.ui.simulation_results_view import (
    _percent as _percent,
)
from ticker_analyzer.ui.simulation_results_view import (
    _ratio as _ratio,
)
from ticker_analyzer.ui.simulation_results_view import (
    _render_risk_analytics as _render_risk_analytics,
)
from ticker_analyzer.ui.simulation_results_view import (
    _render_simulation_comparison as _render_simulation_comparison,
)

BASE_CURRENCIES = ("USD", "EUR", "PLN")


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
        st.caption(
            f"Tickers: {sum(weights.values()):.2%}; cash: {assumptions.cash_weight:.2%}; total: {sum(weights.values()) + assumptions.cash_weight:.2%}"
        )
    show_positions = st.checkbox(
        "Show individual ticker lines",
        value=True,
        key="simulation_show_positions",
    )

    signature = (
        tuple(tickers),
        initial_capital,
        start_date,
        end_date,
        base_currency,
        tuple(weights.items()),
        assumptions,
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
        cash_weight = (
            float(
                strategy[3].number_input(
                    "Target cash (%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=0.0,
                    step=1.0,
                    key="simulation_cash_weight",
                )
            )
            / 100
        )

        costs = st.columns(4)
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
        inflation = _percent_input(costs[3], "Inflation p.a. (%)", "simulation_inflation")
        risk = st.columns(3)
        capital_gains_tax = _percent_input(risk[0], "Capital gains tax (%)", "simulation_gains_tax")
        dividend_tax = _percent_input(risk[1], "Dividend tax (%)", "simulation_dividend_tax")
        risk_free_rate = _percent_input(risk[2], "Risk-free rate p.a. (%)", "simulation_risk_free_rate")
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
        annual_risk_free_rate_percent=risk_free_rate,
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
