from __future__ import annotations

import math
from typing import Any

import pandas as pd

from ticker_analyzer.metrics.utils import (
    aligned_ratio_observations,
    cagr_pct,
    clean_number,
    latest_row_value,
    median_or_none,
    metric_value,
    range_median_note,
    range_ratio_metric,
    row_values,
    statement_ratio_median,
    statement_ratio_observations,
    value_on_or_before,
)


def is_financial_company(info: dict[str, Any]) -> bool:
    industry = str(info.get("industry") or info.get("industryDisp") or "").lower()
    quote_type = str(info.get("quoteType") or "").lower()
    financial_industries = [
        "bank",
        "insurance",
        "asset management",
        "capital markets",
        "credit services",
        "financial services",
        "mortgage",
        "reit",
    ]
    return quote_type == "equity" and any(keyword in industry for keyword in financial_industries)


def operating_margin(income: pd.DataFrame) -> float | None:
    operating_income = latest_row_value(income, ["Operating Income"])
    revenue = latest_row_value(income, ["Total Revenue", "Operating Revenue"])
    if revenue in (None, 0) or operating_income is None:
        return None
    return operating_income / revenue * 100


def operating_margin_trend(income: pd.DataFrame, years: int) -> float | None:
    operating_income = row_values(income, ["Operating Income"])
    revenue = row_values(income, ["Total Revenue", "Operating Revenue"])
    if len(operating_income) < years + 1:
        return None
    current_date, base_date = operating_income.index[-1], operating_income.index[-(years + 1)]
    current_revenue, base_revenue = value_on_or_before(revenue, current_date), value_on_or_before(revenue, base_date)
    current_income = clean_number(operating_income.iloc[-1])
    base_income = clean_number(operating_income.iloc[-(years + 1)])
    if current_income is None or base_income is None or current_revenue in (None, 0) or base_revenue in (None, 0):
        return None
    return (current_income / current_revenue - base_income / base_revenue) * 100


def gross_margin_trend(income: pd.DataFrame, years: int) -> float | None:
    gross_profit = row_values(income, ["Gross Profit"])
    revenue = row_values(income, ["Total Revenue", "Operating Revenue"])
    if len(gross_profit) < years + 1:
        return None
    current_date = gross_profit.index[-1]
    base_date = gross_profit.index[-(years + 1)]
    current_revenue = value_on_or_before(revenue, current_date)
    base_revenue = value_on_or_before(revenue, base_date)
    current_profit = clean_number(gross_profit.iloc[-1])
    base_profit = clean_number(gross_profit.iloc[-(years + 1)])
    if current_profit is None or base_profit is None or current_revenue in (None, 0) or base_revenue in (None, 0):
        return None
    return (current_profit / current_revenue - base_profit / base_revenue) * 100


def series_coefficient_of_variation(series: pd.Series, *, minimum_observations: int = 3) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < minimum_observations:
        return None
    mean_value = float(values.mean())
    if abs(mean_value) < 1e-9:
        return None
    return float(values.std(ddof=0) / abs(mean_value))


def growth_stability(frame: pd.DataFrame, row_names: list[str], years: int) -> float | None:
    values = row_values(frame, row_names).tail(max(3, years + 1))
    growth = values.pct_change(fill_method=None).replace([math.inf, -math.inf], math.nan)
    return series_coefficient_of_variation(growth)


def ratio_stability(
    numerator_frame: pd.DataFrame,
    numerator_names: list[str],
    denominator_frame: pd.DataFrame,
    denominator_names: list[str],
    years: int,
) -> float | None:
    numerator = row_values(numerator_frame, numerator_names).tail(max(3, years + 1))
    denominator = row_values(denominator_frame, denominator_names).tail(max(3, years + 1))
    aligned = pd.concat([numerator.rename("numerator"), denominator.rename("denominator")], axis=1).dropna()
    aligned = aligned[aligned["denominator"] != 0]
    return series_coefficient_of_variation(aligned["numerator"] / aligned["denominator"])


def gross_profit_to_assets(income: pd.DataFrame, balance: pd.DataFrame, years: int) -> float | None:
    return statement_ratio_median(
        income,
        ["Gross Profit"],
        balance,
        ["Total Assets"],
        years,
        multiplier=100,
    )


def share_count_cagr(balance: pd.DataFrame, years: int) -> float | None:
    shares = row_values(balance, ["Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding"])
    if len(shares) < years + 1:
        return None
    return cagr_pct(shares.iloc[-1], shares.iloc[-(years + 1)], years)


def debt_to_assets(balance: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        balance,
        ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
        balance,
        ["Total Assets"],
        years,
        multiplier=100,
    )


def equity_to_assets(balance: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        balance,
        ["Stockholders Equity", "Total Equity Gross Minority Interest"],
        balance,
        ["Total Assets"],
        years,
        multiplier=100,
    )


def return_on_assets(income: pd.DataFrame, balance: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        income,
        ["Net Income", "Net Income Common Stockholders"],
        balance,
        ["Total Assets"],
        years,
        multiplier=100,
    )


def return_on_equity(income: pd.DataFrame, balance: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        income,
        ["Net Income", "Net Income Common Stockholders"],
        balance,
        ["Stockholders Equity", "Total Equity Gross Minority Interest"],
        years,
        multiplier=100,
    )


def net_margin(income: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        income,
        ["Net Income", "Net Income Common Stockholders"],
        income,
        ["Total Revenue", "Operating Revenue"],
        years,
        multiplier=100,
    )


def build_fundamentals_metrics(
    info: dict[str, Any],
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    quarterly_balance: pd.DataFrame,
    years: int,
) -> dict[str, dict[str, Any]]:
    return {
        "debt_to_assets": range_ratio_metric(
            statement_ratio_observations(
                balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
                balance, ["Total Assets"], years, multiplier=100,
            ),
            years,
        ),
        "quick_ratio": quick_ratio_range_metric(info, quarterly_balance, balance, years),
        "cfo_to_debt": range_ratio_metric(
            statement_ratio_observations(
                cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"],
                balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
                years, zero_denominator_cap=10.0,
            ),
            years,
        ),
        "interest_coverage": range_ratio_metric(
            statement_ratio_observations(
                income, ["Operating Income", "EBIT"],
                income, ["Interest Expense", "Interest Expense Non Operating"],
                years, absolute_denominator=True,
            ),
            years,
        ),
        "equity_to_assets": range_ratio_metric(
            statement_ratio_observations(
                balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"],
                balance, ["Total Assets"], years, multiplier=100,
            ),
            years,
            "Financial profile capital buffer metric",
        ),
        "return_on_assets": range_ratio_metric(
            statement_ratio_observations(
                income, ["Net Income", "Net Income Common Stockholders"],
                balance, ["Total Assets"], years, multiplier=100,
            ),
            years,
            "Financial profile profitability metric",
        ),
        "return_on_equity": range_ratio_metric(
            statement_ratio_observations(
                income, ["Net Income", "Net Income Common Stockholders"],
                balance, ["Stockholders Equity", "Total Equity Gross Minority Interest"],
                years, multiplier=100,
            ),
            years,
            "Financial profile profitability metric",
        ),
        "net_margin": range_ratio_metric(
            statement_ratio_observations(
                income, ["Net Income", "Net Income Common Stockholders"],
                income, ["Total Revenue", "Operating Revenue"], years, multiplier=100,
            ),
            years,
            "Financial profile profitability metric",
        ),
        "roic": range_ratio_metric(roic_observations(income, balance, years), years),
        "fcf_margin": range_ratio_metric(fcf_margin_observations(income, cashflow, years), years),
        "accruals_ratio": range_ratio_metric(accruals_ratio_observations(income, balance, cashflow, years), years),
        "net_debt_to_ebitda": range_ratio_metric(net_debt_to_ebitda_observations(income, balance, years), years),
    }


def quick_ratio_range_metric(
    info: dict[str, Any],
    quarterly_balance: pd.DataFrame,
    annual_balance: pd.DataFrame,
    years: int,
) -> dict[str, Any]:
    observations = quick_ratio_observations(annual_balance, years)
    minimum = 1 if years == 1 else 2
    if len(observations) >= minimum:
        return metric_value(median_or_none(observations), range_median_note(years, len(observations)))
    reported = clean_number(info.get("quickRatio"))
    if years == 1 and reported is not None:
        return metric_value(reported, "Latest reported yfinance quickRatio; annual statement ratio unavailable")
    fallback = quick_ratio(info, quarterly_balance, pd.DataFrame(), 1)
    if years == 1 and fallback is not None:
        return metric_value(fallback, "Latest quarterly balance-sheet fallback; annual statement ratio unavailable")
    return metric_value(None, f"{range_median_note(years, len(observations))}; requires at least {minimum} observation(s)")


def quick_ratio(
    info: dict[str, Any],
    quarterly_balance: pd.DataFrame,
    annual_balance: pd.DataFrame,
    years: int = 1,
) -> float | None:
    historical = quick_ratio_median(annual_balance, years)
    if historical is not None:
        return historical
    reported = clean_number(info.get("quickRatio"))
    if reported is not None:
        return reported
    balance = quarterly_balance if not quarterly_balance.empty else annual_balance
    cash_and_investments = latest_row_value(balance, ["Cash Cash Equivalents And Short Term Investments"])
    if cash_and_investments is None:
        cash = latest_row_value(balance, ["Cash And Cash Equivalents"]) or 0
        short_term_investments = latest_row_value(balance, ["Other Short Term Investments"]) or 0
        cash_and_investments = cash + short_term_investments
    receivables = latest_row_value(balance, ["Receivables", "Accounts Receivable"]) or 0
    liabilities = latest_row_value(balance, ["Current Liabilities", "Total Current Liabilities"])
    if liabilities in (None, 0):
        return None
    return (cash_and_investments + receivables) / liabilities


def quick_ratio_median(balance: pd.DataFrame, years: int) -> float | None:
    return median_or_none(quick_ratio_observations(balance, years))


def quick_ratio_observations(balance: pd.DataFrame, years: int) -> list[float]:
    liabilities = row_values(balance, ["Current Liabilities", "Total Current Liabilities"])
    if liabilities.empty:
        return []
    combined_cash = row_values(balance, ["Cash Cash Equivalents And Short Term Investments"])
    cash = row_values(balance, ["Cash And Cash Equivalents"])
    investments = row_values(balance, ["Other Short Term Investments"])
    receivables = row_values(balance, ["Receivables", "Accounts Receivable"])
    ratios: list[float] = []
    for date, liability in liabilities.tail(years).items():
        liability = clean_number(liability)
        if liability is None or liability <= 0:
            continue
        liquid = value_on_or_before(combined_cash, date)
        if liquid is None:
            liquid = (value_on_or_before(cash, date) or 0) + (value_on_or_before(investments, date) or 0)
        receivable = value_on_or_before(receivables, date) or 0
        ratios.append((liquid + receivable) / liability)
    return ratios


def roic_observations(income: pd.DataFrame, balance: pd.DataFrame, years: int) -> list[float]:
    ebit = row_values(income, ["EBIT", "Operating Income"])
    tax_rates = row_values(income, ["Tax Rate For Calcs"])
    invested_capital = row_values(balance, ["Invested Capital"])
    values: list[float] = []
    for date, ebit_value in ebit.tail(years).items():
        capital = value_on_or_before(invested_capital, date)
        tax_rate = value_on_or_before(tax_rates, date)
        ebit_number = clean_number(ebit_value)
        if ebit_number is None or capital is None or capital <= 0:
            continue
        if tax_rate is None:
            normalized_tax_rate = 0.21
        else:
            normalized_tax_rate = tax_rate / 100 if tax_rate > 1 else tax_rate
            normalized_tax_rate = min(max(normalized_tax_rate, 0), 1)
        values.append(ebit_number * (1 - normalized_tax_rate) / capital * 100)
    return values


def fcf_margin_observations(income: pd.DataFrame, cashflow: pd.DataFrame, years: int) -> list[float]:
    revenue = row_values(income, ["Total Revenue", "Operating Revenue"])
    free_cash_flow = free_cash_flow_series(cashflow)
    return aligned_ratio_observations(free_cash_flow, revenue, years, multiplier=100)


def accruals_ratio_observations(
    income: pd.DataFrame,
    balance: pd.DataFrame,
    cashflow: pd.DataFrame,
    years: int,
) -> list[float]:
    net_income = row_values(income, ["Net Income", "Net Income Common Stockholders"])
    cfo = row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    assets = row_values(balance, ["Total Assets"])
    values: list[float] = []
    for date, net_income_value in net_income.tail(years).items():
        ni = clean_number(net_income_value)
        operating_cash = value_on_or_before(cfo, date)
        total_assets = value_on_or_before(assets, date)
        if ni is None or operating_cash is None or total_assets in (None, 0):
            continue
        values.append((ni - operating_cash) / total_assets * 100)
    return values


def net_debt_to_ebitda_observations(income: pd.DataFrame, balance: pd.DataFrame, years: int) -> list[float]:
    ebitda = row_values(income, ["EBITDA", "Normalized EBITDA"])
    net_debt = row_values(balance, ["Net Debt"])
    debt = row_values(balance, ["Total Debt", "Long Term Debt And Capital Lease Obligation"])
    cash = row_values(balance, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])
    values: list[float] = []
    for date, ebitda_value in ebitda.tail(years).items():
        denominator = clean_number(ebitda_value)
        if denominator is None or denominator <= 0:
            continue
        numerator = value_on_or_before(net_debt, date)
        if numerator is None:
            total_debt = value_on_or_before(debt, date)
            cash_value = value_on_or_before(cash, date) or 0
            if total_debt is None:
                continue
            numerator = total_debt - cash_value
        values.append(numerator / denominator)
    return values


def free_cash_flow_series(cashflow: pd.DataFrame) -> pd.Series:
    reported = row_values(cashflow, ["Free Cash Flow"])
    if not reported.empty:
        return reported
    cfo = row_values(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    capex = row_values(cashflow, ["Capital Expenditure", "Capital Expenditures"])
    if cfo.empty or capex.empty:
        return pd.Series(dtype=float)
    values: dict[Any, float] = {}
    for date, cfo_value in cfo.items():
        operating_cash = clean_number(cfo_value)
        capital_expenditure = value_on_or_before(capex, date)
        if operating_cash is None or capital_expenditure is None:
            continue
        values[date] = operating_cash + capital_expenditure if capital_expenditure < 0 else operating_cash - capital_expenditure
    return pd.Series(values, dtype=float)


def cfo_to_debt(cashflow: pd.DataFrame, balance: pd.DataFrame, years: int = 1, cap_if_debt_free: float = 10.0) -> float | None:
    return statement_ratio_median(
        cashflow,
        ["Operating Cash Flow", "Total Cash From Operating Activities"],
        balance,
        ["Total Debt", "Long Term Debt And Capital Lease Obligation"],
        years,
        zero_denominator_cap=cap_if_debt_free,
    )


def interest_coverage(income: pd.DataFrame, years: int = 1) -> float | None:
    return statement_ratio_median(
        income,
        ["Operating Income", "EBIT"],
        income,
        ["Interest Expense", "Interest Expense Non Operating"],
        years,
        absolute_denominator=True,
    )


def ohlson_probability(income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame) -> float | None:
    assets = latest_row_value(balance, ["Total Assets"])
    liabilities = latest_row_value(balance, ["Total Liabilities Net Minority Interest", "Total Liab"])
    current_assets = latest_row_value(balance, ["Current Assets", "Total Current Assets"])
    current_liabilities = latest_row_value(balance, ["Current Liabilities", "Total Current Liabilities"])
    net_income = latest_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    prior_net_income = prior_row_value(income, ["Net Income", "Net Income Common Stockholders"])
    cfo = latest_row_value(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities"])
    working_capital = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities
    required = [assets, liabilities, current_assets, current_liabilities, net_income, prior_net_income, cfo, working_capital]
    if any(value is None for value in required) or assets == 0:
        return None
    size = math.log(max(assets / 1_000_000, 1))
    tlta = liabilities / assets
    wcta = working_capital / assets
    clca = current_liabilities / current_assets if current_assets else None
    nita = net_income / assets
    futl = cfo / liabilities if liabilities else None
    intwo = 1 if net_income < 0 and prior_net_income < 0 else 0
    oeneg = 1 if liabilities > assets else 0
    chin = (net_income - prior_net_income) / (abs(net_income) + abs(prior_net_income))
    if clca is None or futl is None:
        return None
    score = -1.32 - 0.407 * size + 6.03 * tlta - 1.43 * wcta + 0.076 * clca - 1.72 * oeneg - 2.37 * nita - 1.83 * futl + 0.285 * intwo - 0.521 * chin
    return 1 / (1 + math.exp(-score)) * 100


def prior_row_value(frame: pd.DataFrame, names: list[str]) -> float | None:
    values = row_values(frame, names)
    if len(values) < 2:
        return None
    return clean_number(values.iloc[-2])
