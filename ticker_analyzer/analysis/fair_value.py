from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any

from ticker_analyzer.numbers import clean_number

SCENARIO_NAMES = ("Bear", "Base", "Bull")


@dataclass(frozen=True)
class FairValueInputs:
    current_price: float | None
    currency: str
    revenue_per_share: float | None
    free_cash_flow_per_share: float | None
    earnings_per_share: float | None
    dividend_per_share: float | None
    current_fcf_margin_percent: float | None


@dataclass(frozen=True)
class FairValueScenario:
    name: str
    revenue_growth_percent: float
    target_fcf_margin_percent: float
    earnings_growth_percent: float
    earnings_multiple: float
    fcf_growth_percent: float
    fcf_multiple: float
    dividend_growth_percent: float


@dataclass(frozen=True)
class FairValueAssumptions:
    horizon_years: int
    discount_rate_percent: float
    terminal_growth_percent: float
    scenarios: tuple[FairValueScenario, ...]


@dataclass(frozen=True)
class FairValueEstimate:
    method: str
    scenario: str
    value: float | None
    note: str


@dataclass(frozen=True)
class FairValueResult:
    inputs: FairValueInputs
    estimates: tuple[FairValueEstimate, ...]
    consensus: dict[str, float | None]
    range_low: float | None
    range_high: float | None
    base_value: float | None


def inputs_from_analysis(result: dict[str, Any]) -> FairValueInputs:
    price = _positive(result.get("current_price"))
    raw = result.get("raw", {}) if isinstance(result.get("raw"), dict) else {}
    current_pe = _raw_value(raw, "pe_current")
    current_ps = _raw_value(raw, "price_to_sales_current")
    fcf_yield = _raw_value(raw, "fcf_yield_ttm")
    eps = _positive(_raw_value(raw, "fair_value_eps"))
    if eps is None and price is not None and current_pe is not None and current_pe > 0:
        eps = price / current_pe
    revenue_per_share = price / current_ps if price is not None and current_ps is not None and current_ps > 0 else None
    fcf_per_share = price * fcf_yield / 100 if price is not None and fcf_yield is not None else None
    fcf_margin = None
    if revenue_per_share not in (None, 0) and fcf_per_share is not None:
        fcf_margin = fcf_per_share / revenue_per_share * 100
    if fcf_margin is None:
        fcf_margin = _raw_value(raw, "fcf_margin")
    return FairValueInputs(
        current_price=price,
        currency=str(result.get("currency") or ""),
        revenue_per_share=_positive(revenue_per_share),
        free_cash_flow_per_share=clean_number(fcf_per_share),
        earnings_per_share=eps,
        dividend_per_share=_positive(_raw_value(raw, "fair_value_dividend_per_share")),
        current_fcf_margin_percent=clean_number(fcf_margin),
    )


def default_assumptions(result: dict[str, Any]) -> FairValueAssumptions:
    raw = result.get("raw", {}) if isinstance(result.get("raw"), dict) else {}
    inputs = inputs_from_analysis(result)
    revenue_growth = _clamp(_raw_value(raw, "revenue_estimate_growth"), -5, 20, fallback=5)
    earnings_growth = _clamp(_raw_value(raw, "eps_estimate_avg_growth"), -5, 25, fallback=7)
    fcf_growth = _clamp(_raw_value(raw, "cfo_range_growth"), -5, 20, fallback=revenue_growth)
    current_margin = _clamp(inputs.current_fcf_margin_percent, -10, 40, fallback=10)
    current_pe = _clamp(_raw_value(raw, "pe_current"), 8, 25, fallback=16)
    profile = str(result.get("profile") or "")
    if profile.startswith("Financial"):
        current_pe = _clamp(current_pe, 8, 18, fallback=13)
    base_fcf_multiple = 16.0
    return FairValueAssumptions(
        horizon_years=5,
        discount_rate_percent=10.0,
        terminal_growth_percent=2.0,
        scenarios=(
            FairValueScenario(
                "Bear",
                revenue_growth - 5,
                current_margin - 3,
                earnings_growth - 5,
                max(6, current_pe - 4),
                fcf_growth - 5,
                base_fcf_multiple - 5,
                0.0,
            ),
            FairValueScenario(
                "Base",
                revenue_growth,
                current_margin,
                earnings_growth,
                current_pe,
                fcf_growth,
                base_fcf_multiple,
                3.0,
            ),
            FairValueScenario(
                "Bull",
                revenue_growth + 5,
                current_margin + 3,
                earnings_growth + 5,
                current_pe + 4,
                fcf_growth + 5,
                base_fcf_multiple + 5,
                6.0,
            ),
        ),
    )


def calculate_fair_value(
    inputs: FairValueInputs,
    assumptions: FairValueAssumptions,
) -> FairValueResult:
    _validate_assumptions(assumptions)
    estimates: list[FairValueEstimate] = []
    for scenario in assumptions.scenarios:
        estimates.extend(
            (
                _dcf_estimate(inputs, assumptions, scenario),
                _multiple_estimate(
                    "Earnings multiple",
                    scenario.name,
                    inputs.earnings_per_share,
                    scenario.earnings_growth_percent,
                    scenario.earnings_multiple,
                    assumptions,
                ),
                _multiple_estimate(
                    "FCF multiple",
                    scenario.name,
                    inputs.free_cash_flow_per_share,
                    scenario.fcf_growth_percent,
                    scenario.fcf_multiple,
                    assumptions,
                ),
                _ddm_estimate(inputs, assumptions, scenario),
            )
        )
    consensus = {
        name: _median_positive(estimate.value for estimate in estimates if estimate.scenario == name)
        for name in SCENARIO_NAMES
    }
    available_consensus = [value for value in consensus.values() if value is not None]
    return FairValueResult(
        inputs=inputs,
        estimates=tuple(estimates),
        consensus=consensus,
        range_low=min(available_consensus) if available_consensus else None,
        range_high=max(available_consensus) if available_consensus else None,
        base_value=consensus.get("Base"),
    )


def _dcf_estimate(
    inputs: FairValueInputs,
    assumptions: FairValueAssumptions,
    scenario: FairValueScenario,
) -> FairValueEstimate:
    if inputs.revenue_per_share is None:
        return FairValueEstimate("DCF", scenario.name, None, "Revenue per share is unavailable.")
    discount = assumptions.discount_rate_percent / 100
    terminal_growth = assumptions.terminal_growth_percent / 100
    if terminal_growth >= discount:
        return FairValueEstimate("DCF", scenario.name, None, "Terminal growth must be below the discount rate.")
    current_margin = inputs.current_fcf_margin_percent
    if current_margin is None:
        current_margin = scenario.target_fcf_margin_percent
    revenue = inputs.revenue_per_share
    present_value = 0.0
    final_fcf = 0.0
    for year in range(1, assumptions.horizon_years + 1):
        revenue *= 1 + scenario.revenue_growth_percent / 100
        progress = year / assumptions.horizon_years
        margin = current_margin + (scenario.target_fcf_margin_percent - current_margin) * progress
        final_fcf = revenue * margin / 100
        present_value += final_fcf / (1 + discount) ** year
    if final_fcf <= 0:
        return FairValueEstimate("DCF", scenario.name, None, "Projected terminal free cash flow is not positive.")
    terminal_value = final_fcf * (1 + terminal_growth) / (discount - terminal_growth)
    value = present_value + terminal_value / (1 + discount) ** assumptions.horizon_years
    return FairValueEstimate(
        "DCF",
        scenario.name,
        _valid_value(value),
        f"{assumptions.horizon_years}-year revenue/FCF-margin projection plus a Gordon-growth terminal value.",
    )


def _multiple_estimate(
    method: str,
    scenario_name: str,
    per_share_value: float | None,
    growth_percent: float,
    multiple: float,
    assumptions: FairValueAssumptions,
) -> FairValueEstimate:
    if per_share_value is None or per_share_value <= 0:
        return FairValueEstimate(method, scenario_name, None, f"Positive {method.split()[0]} per share is unavailable.")
    future_value = per_share_value * (1 + growth_percent / 100) ** assumptions.horizon_years
    terminal_price = future_value * multiple
    discounted = terminal_price / (1 + assumptions.discount_rate_percent / 100) ** assumptions.horizon_years
    return FairValueEstimate(
        method,
        scenario_name,
        _valid_value(discounted),
        f"Year-{assumptions.horizon_years} per-share value discounted to today.",
    )


def _ddm_estimate(
    inputs: FairValueInputs,
    assumptions: FairValueAssumptions,
    scenario: FairValueScenario,
) -> FairValueEstimate:
    dividend = inputs.dividend_per_share
    if dividend is None:
        return FairValueEstimate("Dividend discount", scenario.name, None, "A positive annual dividend is unavailable.")
    required_return = assumptions.discount_rate_percent / 100
    growth = scenario.dividend_growth_percent / 100
    if growth >= required_return:
        return FairValueEstimate(
            "Dividend discount",
            scenario.name,
            None,
            "Dividend growth must be below the required return.",
        )
    value = dividend * (1 + growth) / (required_return - growth)
    return FairValueEstimate(
        "Dividend discount",
        scenario.name,
        _valid_value(value),
        "Constant-growth Gordon dividend model.",
    )


def _validate_assumptions(assumptions: FairValueAssumptions) -> None:
    if assumptions.horizon_years < 1 or assumptions.horizon_years > 20:
        raise ValueError("Fair Value horizon must be between 1 and 20 years.")
    if assumptions.discount_rate_percent <= 0:
        raise ValueError("Discount rate must be positive.")
    if {scenario.name for scenario in assumptions.scenarios} != set(SCENARIO_NAMES):
        raise ValueError("Fair Value requires Bear, Base and Bull scenarios.")
    for scenario in assumptions.scenarios:
        if scenario.revenue_growth_percent <= -100 or scenario.earnings_growth_percent <= -100 or scenario.fcf_growth_percent <= -100:
            raise ValueError(f"{scenario.name} growth assumptions must be above -100%.")
        if scenario.earnings_multiple <= 0 or scenario.fcf_multiple <= 0:
            raise ValueError(f"{scenario.name} valuation multiples must be positive.")


def _raw_value(raw: dict[str, Any], metric_id: str) -> float | None:
    metric = raw.get(metric_id)
    return clean_number(metric.get("value")) if isinstance(metric, dict) else None


def _positive(value: Any) -> float | None:
    number = clean_number(value)
    return number if number is not None and number > 0 else None


def _clamp(value: Any, minimum: float, maximum: float, *, fallback: float) -> float:
    number = clean_number(value)
    return min(max(number if number is not None else fallback, minimum), maximum)


def _valid_value(value: float) -> float | None:
    return float(value) if isfinite(value) and value > 0 else None


def _median_positive(values: Any) -> float | None:
    available = [float(value) for value in values if value is not None and value > 0 and isfinite(value)]
    return median(available) if available else None
