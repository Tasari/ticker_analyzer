from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from ticker_analyzer.analysis.fair_value import (
    FairValueAssumptions,
    FairValueScenario,
    calculate_fair_value,
    default_assumptions,
    inputs_from_analysis,
)
from ticker_analyzer.numbers import clean_number


def render_fair_value(result: dict) -> None:
    st.info(
        "Fair Value is independent of the Value score and does not change the stock rating. It is a sensitivity "
        "analysis based on editable assumptions, not a price target or investment recommendation."
    )
    defaults = default_assumptions(result)
    ticker = result["ticker"]
    controls = st.columns(3)
    horizon = int(
        controls[0].number_input(
            "Forecast horizon (years)",
            min_value=1,
            max_value=20,
            value=defaults.horizon_years,
            step=1,
            key=f"fair_value_horizon_{ticker}",
        )
    )
    discount_rate = float(
        controls[1].number_input(
            "Discount / required return (%)",
            min_value=0.1,
            max_value=100.0,
            value=defaults.discount_rate_percent,
            step=0.5,
            key=f"fair_value_discount_{ticker}",
        )
    )
    terminal_growth = float(
        controls[2].number_input(
            "Terminal growth (%)",
            min_value=-20.0,
            max_value=20.0,
            value=defaults.terminal_growth_percent,
            step=0.5,
            key=f"fair_value_terminal_{ticker}",
        )
    )

    st.markdown("#### Scenario assumptions")
    scenario_frame = pd.DataFrame(
        [
            {
                "Scenario": scenario.name,
                "Revenue growth (%)": scenario.revenue_growth_percent,
                "Target FCF margin (%)": scenario.target_fcf_margin_percent,
                "Earnings growth (%)": scenario.earnings_growth_percent,
                "P/E multiple": scenario.earnings_multiple,
                "FCF growth (%)": scenario.fcf_growth_percent,
                "FCF multiple": scenario.fcf_multiple,
                "Dividend growth (%)": scenario.dividend_growth_percent,
            }
            for scenario in defaults.scenarios
        ]
    )
    edited = st.data_editor(
        scenario_frame,
        hide_index=True,
        width="stretch",
        key=f"fair_value_scenarios_{ticker}",
        disabled=["Scenario"],
        column_config={
            "Revenue growth (%)": st.column_config.NumberColumn(format="%.2f"),
            "Target FCF margin (%)": st.column_config.NumberColumn(format="%.2f"),
            "Earnings growth (%)": st.column_config.NumberColumn(format="%.2f"),
            "P/E multiple": st.column_config.NumberColumn(min_value=0.1, format="%.2f"),
            "FCF growth (%)": st.column_config.NumberColumn(format="%.2f"),
            "FCF multiple": st.column_config.NumberColumn(min_value=0.1, format="%.2f"),
            "Dividend growth (%)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    inputs = inputs_from_analysis(result)
    try:
        assumptions = FairValueAssumptions(
            horizon_years=horizon,
            discount_rate_percent=discount_rate,
            terminal_growth_percent=terminal_growth,
            scenarios=tuple(_scenario_from_row(row) for row in edited.to_dict("records")),
        )
        valuation = calculate_fair_value(inputs, assumptions)
    except ValueError as exc:
        st.error(str(exc))
        return

    _render_input_quality(valuation.inputs)
    _render_valuation_summary(valuation, inputs.currency)
    _render_method_results(valuation, inputs.currency)


def _scenario_from_row(row: dict) -> FairValueScenario:
    return FairValueScenario(
        name=str(row["Scenario"]),
        revenue_growth_percent=_required_number(row, "Revenue growth (%)"),
        target_fcf_margin_percent=_required_number(row, "Target FCF margin (%)"),
        earnings_growth_percent=_required_number(row, "Earnings growth (%)"),
        earnings_multiple=_required_number(row, "P/E multiple"),
        fcf_growth_percent=_required_number(row, "FCF growth (%)"),
        fcf_multiple=_required_number(row, "FCF multiple"),
        dividend_growth_percent=_required_number(row, "Dividend growth (%)"),
    )


def _required_number(row: dict, field: str) -> float:
    value = clean_number(row.get(field))
    if value is None:
        raise ValueError(f"{field} must contain a number in every scenario.")
    return value


def _render_input_quality(inputs) -> None:
    with st.expander("Model inputs derived from the analysis", expanded=False):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Input": "Current price", "Value": inputs.current_price},
                    {"Input": "Revenue per share", "Value": inputs.revenue_per_share},
                    {"Input": "Free cash flow per share", "Value": inputs.free_cash_flow_per_share},
                    {"Input": "Earnings per share", "Value": inputs.earnings_per_share},
                    {"Input": "Annual dividend per share", "Value": inputs.dividend_per_share},
                    {"Input": "Current FCF margin (%)", "Value": inputs.current_fcf_margin_percent},
                ]
            ),
            hide_index=True,
            width="stretch",
            column_config={"Value": st.column_config.NumberColumn(format="%.4f")},
        )
        st.caption(
            "Per-share inputs are reconstructed from current price and the analyzer's P/E, P/S and FCF-yield data "
            "where direct per-share data is unavailable."
        )


def _render_valuation_summary(valuation, currency: str) -> None:
    current_price = valuation.inputs.current_price
    base_upside = (
        valuation.base_value / current_price - 1
        if valuation.base_value is not None and current_price not in (None, 0)
        else None
    )
    metrics = st.columns(5)
    metrics[0].metric("Current price", _money(current_price, currency))
    metrics[1].metric("Estimated range", _range(valuation.range_low, valuation.range_high, currency))
    metrics[2].metric("Bear consensus", _money(valuation.consensus.get("Bear"), currency))
    metrics[3].metric("Base consensus", _money(valuation.base_value, currency), _percent(base_upside))
    metrics[4].metric("Bull consensus", _money(valuation.consensus.get("Bull"), currency))
    st.caption(
        "Scenario consensus is the median of the available methods. The displayed range spans the scenario medians, "
        "not a statistical confidence interval."
    )


def _render_method_results(valuation, currency: str) -> None:
    rows = [
        {
            "Method": estimate.method,
            "Scenario": estimate.scenario,
            "Estimated value": estimate.value,
            "Upside / downside": (
                estimate.value / valuation.inputs.current_price - 1
                if estimate.value is not None and valuation.inputs.current_price not in (None, 0)
                else None
            ),
            "Status": "Available" if estimate.value is not None else "Unavailable",
            "Details": estimate.note,
        }
        for estimate in valuation.estimates
    ]
    frame = pd.DataFrame(rows)
    st.markdown("#### Valuation methods")
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Estimated value": st.column_config.NumberColumn(format=f"%.2f {currency}"),
            "Upside / downside": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
    available = frame.dropna(subset=["Estimated value"])
    if available.empty:
        st.warning("None of the Fair Value methods has enough positive input data for this company.")
        return
    figure = px.bar(
        available,
        x="Method",
        y="Estimated value",
        color="Scenario",
        barmode="group",
        category_orders={"Scenario": ["Bear", "Base", "Bull"]},
        title="Fair Value sensitivity by method and scenario",
    )
    if valuation.inputs.current_price is not None:
        figure.add_hline(
            y=valuation.inputs.current_price,
            line_dash="dash",
            annotation_text="Current price",
        )
    figure.update_layout(yaxis_title=f"Estimated value ({currency})", xaxis_title=None)
    st.plotly_chart(figure, width="stretch")


def _money(value: float | None, currency: str) -> str:
    return "N/A" if value is None else f"{value:,.2f} {currency}".strip()


def _range(low: float | None, high: float | None, currency: str) -> str:
    if low is None or high is None:
        return "N/A"
    return f"{low:,.2f} - {high:,.2f} {currency}".strip()


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"
