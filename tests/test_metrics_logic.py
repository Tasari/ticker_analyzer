import unittest

import pandas as pd

from ticker_analyzer.config import ConfigValidationError, normalize_config
from ticker_analyzer.engine import (
    cagr_pct,
    cfo_to_debt,
    company_profile,
    config_for_profile,
    estimate_growth,
    fcf_yield,
    momentum_12_1,
    overall_score_with_missing_policy,
    range_ratio_metric,
    statement_ratio_median,
    ttm_range_cagr,
)
from ticker_analyzer.scoring import classify_tab_rating
from ticker_analyzer.domain import AnalysisRanges


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
        self.assertEqual(cfo_to_debt(cashflow, balance), 10.0)

    def test_ttm_range_cagr_uses_quarterly_ttm_windows(self):
        dates = pd.date_range("2024-03-31", periods=8, freq="QE")
        values = [25, 25, 25, 25, 30.25, 30.25, 30.25, 30.25]
        frame = pd.DataFrame({date: [value] for date, value in zip(dates, values)}, index=["Total Revenue"])
        result, note = ttm_range_cagr(frame, ["Total Revenue"], 1)
        self.assertAlmostEqual(result, 21.0)
        self.assertIn("TTM vs TTM", note)

    def test_statement_ratio_median_changes_with_selected_range(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        numerator = pd.DataFrame({date: [value] for date, value in zip(dates, [10, 30, 90])}, index=["Debt"])
        denominator = pd.DataFrame({date: [100] for date in dates}, index=["Assets"])
        self.assertEqual(statement_ratio_median(numerator, ["Debt"], denominator, ["Assets"], 1, multiplier=100), 90)
        self.assertEqual(statement_ratio_median(numerator, ["Debt"], denominator, ["Assets"], 3, multiplier=100), 30)

    def test_multi_year_range_metric_requires_two_observations(self):
        result = range_ratio_metric([25.0], 4)
        self.assertIsNone(result["value"])
        self.assertIn("1 available annual observation", result["note"])
        self.assertIn("requires at least 2", result["note"])

    def test_default_analysis_range_is_two_years(self):
        ranges = AnalysisRanges.from_input({})
        self.assertEqual(ranges.as_dict(), {"Growth": "2Y", "Fundamentals": "2Y", "Value": "2Y"})

    def test_fcf_yield_uses_free_cash_flow(self):
        cashflow = pd.DataFrame({pd.Timestamp("2025-12-31"): [50]}, index=["Free Cash Flow"])
        self.assertEqual(fcf_yield({"marketCap": 1000}, cashflow), 5.0)

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

    def test_overall_score_can_use_partial_tabs_when_configured(self):
        tab_results = {
            "Growth": {"score": 80},
            "Fundamentals": {"score": None},
            "Value": {"score": 60},
        }
        config = {
            "tab_weights": {"Growth": 1, "Fundamentals": 1, "Value": 1},
            "missing_policy": {"require_all_tabs_for_overall": False, "minimum_scored_tabs": 2},
        }
        self.assertAlmostEqual(overall_score_with_missing_policy(tab_results, config), 70.0)

    def test_overall_score_can_require_all_tabs(self):
        tab_results = {
            "Growth": {"score": 80},
            "Fundamentals": {"score": None},
            "Value": {"score": 60},
        }
        config = {
            "tab_weights": {"Growth": 1, "Fundamentals": 1, "Value": 1},
            "missing_policy": {"require_all_tabs_for_overall": True, "minimum_scored_tabs": 2},
        }
        self.assertIsNone(overall_score_with_missing_policy(tab_results, config))

    def test_company_profile_detects_financial_industries(self):
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Banks - Diversified"}), "Financial")
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Credit Services"}), "Financial")
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Consumer Electronics"}), "Industrial")

    def test_config_for_profile_uses_financial_metric_override(self):
        config = {
            "metrics": {"Growth": [{"id": "industrial"}]},
            "profile_metrics": {"Financial": {"Growth": [{"id": "financial"}]}},
        }
        self.assertEqual(config_for_profile(config, "Financial")["metrics"]["Growth"][0]["id"], "financial")
        self.assertEqual(config_for_profile(config, "Industrial")["metrics"]["Growth"][0]["id"], "industrial")


if __name__ == "__main__":
    unittest.main()
