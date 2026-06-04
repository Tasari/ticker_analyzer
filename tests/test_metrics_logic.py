import unittest

import pandas as pd

from ticker_analyzer.config import ConfigValidationError, normalize_config
from ticker_analyzer.engine import cagr_pct, cfo_to_debt, estimate_growth, momentum_12_1
from ticker_analyzer.scoring import classify_tab_rating


class MetricsLogicTest(unittest.TestCase):
    def test_cagr_pct_annualizes_multi_year_growth(self):
        self.assertAlmostEqual(cagr_pct(121, 100, 2), 10.0)

    def test_cagr_pct_returns_none_for_non_positive_base(self):
        self.assertIsNone(cagr_pct(100, 0, 3))
        self.assertIsNone(cagr_pct(-100, 100, 3))

    def test_momentum_12_1_skips_latest_month(self):
        index = pd.date_range("2024-01-31", periods=14, freq="ME")
        history = pd.DataFrame({"Close": range(100, 114)}, index=index)
        expected = (112 - 101) / 101 * 100
        self.assertAlmostEqual(momentum_12_1(history), expected)

    def test_cfo_to_debt_rewards_debt_free_positive_cash_flow(self):
        cashflow = pd.DataFrame({pd.Timestamp("2025-12-31"): [100]}, index=["Operating Cash Flow"])
        balance = pd.DataFrame({pd.Timestamp("2025-12-31"): [0]}, index=["Total Debt"])
        self.assertEqual(cfo_to_debt(cashflow, balance), 999.0)

    def test_tab_rating_uses_configured_tab_thresholds(self):
        config = {
            "rating_thresholds": {"very_strong": 90, "strong": 70, "neutral": 50, "weak": 30},
            "tab_rating_thresholds": {"Value": {"very_strong": 85}},
            "tab_rating_labels": {
                "Value": {
                    "very_strong": "Very Underpriced",
                    "strong": "Underpriced",
                    "neutral": "Fair",
                    "weak": "Overpriced",
                    "very_weak": "Very Overpriced",
                }
            },
        }
        self.assertEqual(classify_tab_rating("Value", 86, config), "Very Underpriced")
        self.assertEqual(classify_tab_rating("Value", 80, config), "Underpriced")

    def test_estimate_growth_prefers_structured_table(self):
        table = pd.DataFrame(
            {
                "avg": [100.0, 115.0],
                "numberOfAnalysts": [8, 9],
            },
            index=["0y", "+1y"],
        )
        self.assertAlmostEqual(estimate_growth({}, "revenue", table), 15.0)

    def test_estimate_growth_rejects_low_analyst_count(self):
        table = pd.DataFrame(
            {
                "avg": [100.0, 150.0],
                "numberOfAnalysts": [2, 2],
            },
            index=["0y", "+1y"],
        )
        self.assertIsNone(estimate_growth({}, "revenue", table))

    def test_normalize_config_rejects_missing_metrics(self):
        with self.assertRaises(ConfigValidationError):
            normalize_config({"tab_weights": {}, "rating_thresholds": {}})


if __name__ == "__main__":
    unittest.main()
