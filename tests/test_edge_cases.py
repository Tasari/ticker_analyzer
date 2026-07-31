from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.metrics.formulas import (
    accruals_ratio_observations,
    free_cash_flow_series,
    interest_coverage,
    ohlson_probability,
    quick_ratio,
    quick_ratio_observations,
    quick_ratio_range_metric,
)
from ticker_analyzer.metrics.valuation import (
    approximate_historical_ratio,
    approximate_historical_ratios,
    current_price_to_book,
    current_price_to_cfo,
    estimate_growth,
    estimate_growth_from_table,
    estimate_growth_note,
    estimate_pair,
    estimate_pair_has_non_positive_value,
    estimate_row,
    fcf_yield,
    growth_from_estimates,
    ratio_vs_history,
    ratio_vs_history_metric,
    target_upside,
)


class EdgeCaseTest(unittest.TestCase):
    def test_quick_ratio_fallbacks_and_observation_filtering(self):
        date = pd.Timestamp("2025-12-31")
        balance = pd.DataFrame(
            {date: [20.0, 5.0, 10.0, 0.0]},
            index=[
                "Cash And Cash Equivalents",
                "Other Short Term Investments",
                "Receivables",
                "Current Liabilities",
            ],
        )
        self.assertEqual(quick_ratio_observations(balance, 1), [])
        balance.loc["Current Liabilities", date] = 25.0
        self.assertEqual(quick_ratio({}, balance, pd.DataFrame()), 1.4)
        self.assertEqual(quick_ratio({"quickRatio": 2.0}, pd.DataFrame(), pd.DataFrame()), 2.0)
        self.assertEqual(quick_ratio_range_metric({"quickRatio": 1.5}, pd.DataFrame(), pd.DataFrame(), 1)["value"], 1.5)
        self.assertIsNone(quick_ratio_range_metric({}, pd.DataFrame(), pd.DataFrame(), 2)["value"])

    def test_cash_flow_accrual_interest_and_ohlson_paths(self):
        dates = pd.to_datetime(["2024-12-31", "2025-12-31"])
        income = pd.DataFrame(
            {dates[0]: [8.0, 12.0, -2.0], dates[1]: [10.0, 15.0, -3.0]},
            index=["Net Income", "Operating Income", "Interest Expense"],
        )
        balance = pd.DataFrame(
            {
                dates[0]: [100.0, 50.0, 30.0, 20.0],
                dates[1]: [110.0, 55.0, 35.0, 22.0],
            },
            index=["Total Assets", "Total Liabilities Net Minority Interest", "Current Assets", "Current Liabilities"],
        )
        cashflow = pd.DataFrame(
            {dates[0]: [9.0, -2.0], dates[1]: [12.0, -3.0]},
            index=["Operating Cash Flow", "Capital Expenditure"],
        )
        self.assertEqual(list(free_cash_flow_series(cashflow)), [7.0, 9.0])
        self.assertEqual(len(accruals_ratio_observations(income, balance, cashflow, 2)), 2)
        self.assertEqual(interest_coverage(income, 1), 5.0)
        probability = ohlson_probability(income, balance, cashflow)
        self.assertIsNotNone(probability)
        self.assertGreaterEqual(probability, 0)
        self.assertIsNone(ohlson_probability(pd.DataFrame(), balance, cashflow))

    def test_estimate_and_simple_valuation_fallbacks(self):
        estimates = pd.DataFrame(
            {"avg": [10.0, 12.0], "numberOfAnalysts": [8, 8], "growth": [None, 0.2]},
            index=["0y", "+1y"],
        )
        self.assertEqual(estimate_growth_from_table(estimates, min_analysts=5), 20.0)
        self.assertEqual(estimate_pair(estimates), (10.0, 12.0))
        self.assertEqual(estimate_row(estimates, "+1y")["avg"], 12.0)
        self.assertFalse(estimate_pair_has_non_positive_value(estimates, min_analysts=5))
        negative = estimates.copy()
        negative.loc["0y", "avg"] = -1.0
        self.assertTrue(estimate_pair_has_non_positive_value(negative, min_analysts=5))
        self.assertIn("turnaround", estimate_growth_note("eps", negative).lower())
        trend = pd.DataFrame({"stockTrend": [0.15]}, index=["+1y"])
        self.assertEqual(growth_from_estimates(trend, period="+1y"), 15.0)
        self.assertEqual(estimate_growth({"revenueCurrentYear": 100, "revenueNextYear": 120}, "revenue"), 20.0)
        self.assertEqual(target_upside({"currentPrice": 10}, {"mean": 12}), 20.0)

        date = pd.Timestamp("2025-12-31")
        cashflow = pd.DataFrame({date: [20.0, -5.0]}, index=["Operating Cash Flow", "Capital Expenditure"])
        balance = pd.DataFrame({date: [50.0]}, index=["Stockholders Equity"])
        self.assertEqual(current_price_to_cfo({"marketCap": 100}, cashflow), 5.0)
        self.assertEqual(fcf_yield({"marketCap": 100}, cashflow), 15.0)
        self.assertEqual(current_price_to_book({"marketCap": 100}, balance), 2.0)
        self.assertEqual(current_price_to_book({"priceToBook": 3.0}, balance), 3.0)

    def test_historical_ratio_wrapper_paths(self):
        dates = pd.to_datetime(["2024-12-31", "2025-12-31"])
        history = pd.DataFrame({"Close": [10.0, 12.0]}, index=dates)
        income = pd.DataFrame({dates[0]: [20.0], dates[1]: [24.0]}, index=["Net Income"])
        balance = pd.DataFrame({dates[0]: [10.0], dates[1]: [10.0]}, index=["Ordinary Shares Number"])
        income.attrs["filed_dates"] = {date: date for date in dates}
        balance.attrs["filed_dates"] = {date: date for date in dates}
        values = approximate_historical_ratios("pe", history, income, balance, pd.DataFrame(), 2)
        self.assertEqual(values, [5.0, 5.0])
        self.assertEqual(approximate_historical_ratio("pe", history, income, balance, pd.DataFrame(), 2), 5.0)
        self.assertEqual(ratio_vs_history(6.0, "pe", history, income, balance, pd.DataFrame(), years=2), 20.0)
        self.assertEqual(ratio_vs_history_metric(6.0, "pe", history, income, balance, pd.DataFrame(), years=2)["value"], 20.0)
        self.assertIsNone(ratio_vs_history(None, "pe", history, income, balance, pd.DataFrame(), years=2))


if __name__ == "__main__":
    unittest.main()
