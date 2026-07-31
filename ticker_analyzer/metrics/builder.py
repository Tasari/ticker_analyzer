from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.metrics.formulas import (
    build_fundamentals_metrics,
    gross_margin_trend,
    gross_profit_to_assets,
    growth_stability,
    ohlson_probability,
    operating_margin,
    operating_margin_trend,
    ratio_stability,
    share_count_cagr,
)
from ticker_analyzer.metrics.utils import (
    cagr_pct,
    clean_number,
    latest_row_value,
    metric_value,
    momentum_12_1,
    percent_change,
    row_values,
    statement_value_years_ago,
    sum_recent,
    sum_window,
    ttm_range_cagr,
)
from ticker_analyzer.metrics.valuation import (
    build_historical_ratio_context,
    current_price_to_book,
    current_price_to_cfo,
    estimate_growth,
    estimate_growth_note,
    fcf_yield,
    statement_aligned_ratio_vs_history_metric,
    target_upside,
)

__all__ = ["build_raw_metrics", "apply_configured_metric_fallbacks", "build_charts_data"]


def build_raw_metrics(
    *,
    info: dict[str, Any],
    annual_income: pd.DataFrame,
    annual_balance: pd.DataFrame,
    annual_cashflow: pd.DataFrame,
    quarterly_income: pd.DataFrame,
    quarterly_balance: pd.DataFrame,
    quarterly_cashflow: pd.DataFrame,
    growth_history: pd.DataFrame,
    value_history: pd.DataFrame,
    analyst_targets: dict[str, Any],
    revenue_estimate: pd.DataFrame,
    earnings_estimate: pd.DataFrame,
    eps_trend: pd.DataFrame,
    growth_estimates: pd.DataFrame,
    range_years: dict[str, int],
) -> dict[str, dict[str, Any]]:
    revenue_ttm = sum_recent(quarterly_income, ["Total Revenue", "Operating Revenue"], 4)
    if revenue_ttm is None:
        revenue_ttm = clean_number(info.get("totalRevenue"))
    if revenue_ttm is None:
        revenue_ttm = latest_row_value(annual_income, ["Total Revenue", "Operating Revenue"])
    revenue_prior_ttm = sum_window(quarterly_income, ["Total Revenue", "Operating Revenue"], 4, 8)
    growth_years = range_years["Growth"]
    fundamentals_years = range_years["Fundamentals"]
    value_years = range_years["Value"]
    revenue_range_base = statement_value_years_ago(annual_income, ["Total Revenue", "Operating Revenue"], growth_years)
    net_income_range_base = statement_value_years_ago(annual_income, ["Net Income", "Net Income Common Stockholders"], growth_years)
    cfo_range_base = statement_value_years_ago(annual_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], growth_years)
    momentum = momentum_12_1(growth_history)
    revenue_range_growth, revenue_range_note = ttm_range_cagr(
        quarterly_income,
        ["Total Revenue", "Operating Revenue"],
        growth_years,
        fallback_current=revenue_ttm,
        fallback_base=revenue_range_base,
    )
    cfo_range_growth, cfo_range_note = ttm_range_cagr(
        quarterly_cashflow,
        ["Operating Cash Flow", "Total Cash From Operating Activities"],
        growth_years,
        fallback_current=latest_row_value(annual_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
        fallback_base=cfo_range_base,
    )

    revenue_estimate_growth = estimate_growth(info, "revenue", revenue_estimate, growth_estimates)
    eps_estimate_growth = estimate_growth(info, "eps", earnings_estimate, growth_estimates)
    market_cap = clean_number(info.get("marketCap"))
    fcf_ttm = sum_recent(quarterly_cashflow, ["Free Cash Flow"], 4)
    if fcf_ttm is None:
        ttm_cfo = sum_recent(quarterly_cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"], 4)
        ttm_capex = sum_recent(quarterly_cashflow, ["Capital Expenditure", "Capital Expenditures"], 4)
        if ttm_cfo is not None and ttm_capex is not None:
            fcf_ttm = ttm_cfo + ttm_capex if ttm_capex < 0 else ttm_cfo - ttm_capex
    fcf_yield_ttm = fcf_ttm / market_cap * 100 if fcf_ttm is not None and market_cap not in (None, 0) else None
    if fcf_yield_ttm is None:
        fcf_yield_ttm = fcf_yield(info, annual_cashflow)
    price_target_upside = target_upside(info, analyst_targets)
    value_context = build_historical_ratio_context(
        value_history,
        annual_income,
        annual_balance,
        annual_cashflow,
        years=value_years,
    )
    fundamentals = build_fundamentals_metrics(
        info,
        annual_income,
        annual_balance,
        annual_cashflow,
        quarterly_balance,
        fundamentals_years,
    )

    raw = {
        "revenue_ttm_range_growth": metric_value(revenue_range_growth, revenue_range_note),
        "revenue_ttm_growth": metric_value(percent_change(revenue_ttm, revenue_prior_ttm), "TTM vs previous TTM"),
        "net_income_range_growth": metric_value(cagr_pct(latest_row_value(annual_income, ["Net Income", "Net Income Common Stockholders"]), net_income_range_base, growth_years), f"Latest annual net income CAGR over {growth_years} fiscal year(s); missing when the base or current value is not positive"),
        "cfo_range_growth": metric_value(cfo_range_growth, cfo_range_note),
        "operating_margin": metric_value(operating_margin(annual_income)),
        "operating_margin_trend": metric_value(
            operating_margin_trend(annual_income, growth_years),
            f"Operating margin change over {growth_years} fiscal year(s)",
        ),
        "gross_margin_trend": metric_value(gross_margin_trend(annual_income, growth_years), f"Gross margin change over {growth_years} fiscal year(s)"),
        "revenue_growth_stability": metric_value(
            growth_stability(annual_income, ["Total Revenue", "Operating Revenue"], growth_years),
            "Coefficient of variation of annual revenue growth; lower is more stable",
        ),
        "fcf_growth_stability": metric_value(
            growth_stability(annual_cashflow, ["Free Cash Flow"], growth_years),
            "Coefficient of variation of annual free-cash-flow growth; lower is more stable",
        ),
        "share_count_cagr": metric_value(share_count_cagr(annual_balance, growth_years), f"Ordinary share count CAGR over {growth_years} fiscal year(s); positive values indicate dilution"),
        "price_change": metric_value(
            momentum,
            "Adjusted-price momentum from month -13 to month -2; requires at least 13 monthly observations",
        ),
        "revenue_estimate_growth": metric_value(revenue_estimate_growth, "Uses structured yfinance revenue_estimate when enough analysts are available, then falls back to info fields"),
        "eps_estimate_avg_growth": metric_value(
            eps_estimate_growth,
            estimate_growth_note("eps", earnings_estimate, info),
        ),
        "debt_to_assets": fundamentals["debt_to_assets"],
        "quick_ratio": fundamentals["quick_ratio"],
        "cfo_to_debt": fundamentals["cfo_to_debt"],
        "interest_coverage": fundamentals["interest_coverage"],
        "ohlson_probability": metric_value(ohlson_probability(annual_income, annual_balance, annual_cashflow), "Ohlson-style informational distress estimate; excluded from scoring until market calibration is validated"),
        "roic": fundamentals["roic"],
        "fcf_margin": fundamentals["fcf_margin"],
        "accruals_ratio": fundamentals["accruals_ratio"],
        "net_debt_to_ebitda": fundamentals["net_debt_to_ebitda"],
        "equity_to_assets": fundamentals["equity_to_assets"],
        "return_on_assets": fundamentals["return_on_assets"],
        "return_on_equity": fundamentals["return_on_equity"],
        "net_margin": fundamentals["net_margin"],
        "gross_profit_to_assets": metric_value(
            gross_profit_to_assets(annual_income, annual_balance, fundamentals_years),
            f"Median gross profit divided by assets over {fundamentals_years} fiscal year(s)",
        ),
        "fcf_margin_stability": metric_value(
            ratio_stability(
                annual_cashflow,
                ["Free Cash Flow"],
                annual_income,
                ["Total Revenue", "Operating Revenue"],
                fundamentals_years,
            ),
            "Coefficient of variation of annual free-cash-flow margin; lower is more stable",
        ),
        "operating_margin_stability": metric_value(
            ratio_stability(
                annual_income,
                ["Operating Income"],
                annual_income,
                ["Total Revenue", "Operating Revenue"],
                fundamentals_years,
            ),
            "Coefficient of variation of annual operating margin; lower is more stable",
        ),
        "ps_vs_selected_median": statement_aligned_ratio_vs_history_metric(
            info,
            "ps",
            value_context,
            fallback_current_ratio=info.get("priceToSalesTrailing12Months"),
        ),
        "pe_vs_selected_median": statement_aligned_ratio_vs_history_metric(
            info,
            "pe",
            value_context,
            fallback_current_ratio=info.get("trailingPE"),
        ),
        "pb_vs_selected_median": statement_aligned_ratio_vs_history_metric(
            info,
            "pb",
            value_context,
            fallback_current_ratio=current_price_to_book(info, annual_balance),
            prefix="Financial profile value metric",
        ),
        "ev_ebitda_vs_selected_median": statement_aligned_ratio_vs_history_metric(
            info,
            "ev_ebitda",
            value_context,
            fallback_current_ratio=info.get("enterpriseToEbitda"),
        ),
        "price_to_cfo_vs_selected_median": statement_aligned_ratio_vs_history_metric(
            info,
            "price_to_cfo",
            value_context,
            fallback_current_ratio=current_price_to_cfo(info, annual_cashflow),
        ),
        "fcf_yield": metric_value(fcf_yield(info, annual_cashflow), "Latest annual free cash flow divided by current market capitalization"),
        "fcf_yield_ttm": metric_value(
            fcf_yield_ttm,
            "Trailing twelve-month free cash flow divided by market capitalization; annual fallback when quarterly data is unavailable",
        ),
        "pe_vs_profile_median": metric_value(None, "Requires a matching versioned peer-calibration artifact"),
        "ev_ebitda_vs_profile_median": metric_value(None, "Requires a matching versioned peer-calibration artifact"),
        "fcf_yield_vs_profile_median": metric_value(None, "Requires a matching versioned peer-calibration artifact"),
        "valuation_growth_adjustment": metric_value(
            clean_number(info.get("trailingPE")) / eps_estimate_growth
            if clean_number(info.get("trailingPE")) is not None and eps_estimate_growth is not None and eps_estimate_growth > 0
            else None,
            "Trailing P/E divided by positive forward EPS growth",
        ),
        "price_target": metric_value(price_target_upside),
        "upside_vs_configured_benchmark": metric_value(None, "Uses configured benchmark because historical analyst upside is unavailable"),
    }
    return raw


def apply_configured_metric_fallbacks(
    raw_metrics: dict[str, dict[str, Any]],
    metric_configs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    updated = dict(raw_metrics)
    config_by_id = {metric_config.get("id"): metric_config for metric_config in metric_configs}
    upside_config = config_by_id.get("upside_vs_configured_benchmark")
    price_target_upside = clean_number(updated.get("price_target", {}).get("value"))
    benchmark = clean_number(upside_config.get("benchmark")) if upside_config else None
    if price_target_upside is not None and benchmark is not None:
        updated["upside_vs_configured_benchmark"] = metric_value(
            price_target_upside - benchmark,
            f"Current price target upside minus configured benchmark ({benchmark:.2f}%)",
        )
        updated["upside_vs_configured_benchmark"]["provenance"] = updated.get("price_target", {}).get("provenance")
    return updated


def build_charts_data(
    income: pd.DataFrame,
    cashflow: pd.DataFrame,
    balance: pd.DataFrame,
    history: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    financials = pd.DataFrame(
        {
            "Revenue": row_values(income, ["Total Revenue", "Operating Revenue"]),
            "Net Income": row_values(income, ["Net Income", "Net Income Common Stockholders"]),
            "Operating Cash Flow": row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"]),
        }
    ).dropna(how="all")
    fundamentals = pd.DataFrame(
        {
            "Total Assets": row_values(balance, ["Total Assets"]),
            "Total Debt": row_values(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"]),
        }
    ).dropna(how="all")
    prices = pd.DataFrame()
    if not history.empty and "Close" in history:
        prices = history[["Close"]].dropna()
    return {
        "financials": financials,
        "fundamentals": fundamentals,
        "prices": prices,
    }
