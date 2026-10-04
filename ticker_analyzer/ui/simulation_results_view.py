"""Presentation of strategy results, positions and risk analytics."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.portfolio.advanced_simulation import AdvancedSimulationResult, SimulationComparison
from ticker_analyzer.portfolio.simulation import TRAILING_RETURN_PERIODS
from ticker_analyzer.ui.formatting import money as _money
from ticker_analyzer.ui.formatting import percent as _percent
from ticker_analyzer.ui.formatting import ratio as _ratio


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
                "Sharpe": result.sharpe_ratio,
                "Sortino": result.sortino_ratio,
                "Calmar": result.calmar_ratio,
                "VaR 95% (daily)": result.value_at_risk_95 * 100 if result.value_at_risk_95 is not None else None,
                "Expected Shortfall 95%": (
                    result.expected_shortfall_95 * 100 if result.expected_shortfall_95 is not None else None
                ),
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
            "Sharpe": st.column_config.NumberColumn(format="%.2f"),
            "Sortino": st.column_config.NumberColumn(format="%.2f"),
            "Calmar": st.column_config.NumberColumn(format="%.2f"),
            "VaR 95% (daily)": st.column_config.NumberColumn(format="%.2f%%"),
            "Expected Shortfall 95%": st.column_config.NumberColumn(format="%.2f%%"),
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

    _render_risk_analytics(result)

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
                **{label: value * 100 if value is not None else None for label, value in position.trailing_returns},
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
            **{label: st.column_config.NumberColumn(format="%.2f%%") for label, _ in TRAILING_RETURN_PERIODS},
        },
    )


def _render_risk_analytics(result: AdvancedSimulationResult) -> None:
    st.markdown("#### Risk analytics")
    ratios = st.columns(6)
    ratios[0].metric("Sharpe", _ratio(result.sharpe_ratio))
    ratios[1].metric("Sortino", _ratio(result.sortino_ratio))
    ratios[2].metric("Calmar", _ratio(result.calmar_ratio))
    ratios[3].metric("Downside deviation", _percent(result.downside_deviation))
    ratios[4].metric("Daily VaR 95%", _percent(result.value_at_risk_95))
    ratios[5].metric("Daily Expected Shortfall 95%", _percent(result.expected_shortfall_95))
    st.caption(
        "Sharpe and Sortino use the configured risk-free rate. VaR and Expected Shortfall are historical daily "
        "loss estimates; they are not maximum-loss guarantees."
    )

    periods = st.columns(4)
    periods[0].metric(
        "Worst month",
        result.worst_month or "N/A",
        _percent(result.worst_month_return),
        delta_color="off",
    )
    periods[1].metric(
        "Worst year",
        result.worst_year or "N/A",
        _percent(result.worst_year_return),
        delta_color="off",
    )
    periods[2].metric("Longest drawdown", f"{result.longest_drawdown_days} days")
    periods[3].metric(
        "Recovery from max drawdown",
        (
            f"{result.maximum_drawdown_recovery_days} days"
            if result.maximum_drawdown_recovery_days is not None
            else "Not recovered"
        ),
    )

    correlation = result.correlation_matrix.dropna(axis=0, how="all").dropna(axis=1, how="all")
    st.markdown("##### Component correlations")
    if correlation.empty:
        st.caption("Not enough overlapping observations to calculate component correlations.")
        return
    figure = px.imshow(
        correlation,
        text_auto=".2f",
        zmin=-1,
        zmax=1,
        color_continuous_scale="RdBu_r",
        aspect="auto",
    )
    figure.update_layout(coloraxis_colorbar_title="Correlation")
    st.plotly_chart(figure, width="stretch")
