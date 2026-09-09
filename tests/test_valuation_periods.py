from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.metrics.periods import free_cash_flow_periods, valuation_periods
from ticker_analyzer.metrics.valuation import (
    build_historical_ratio_context,
    current_valuation_multiple,
    statement_aligned_ratio_vs_history_metric,
)


def statements(values, dates, row="Net Income"):
    return pd.DataFrame([values], index=[row], columns=pd.to_datetime(dates))


class ValuationPeriodTests(unittest.TestCase):
    def test_analysis_entry_points_remain_available_with_metrics_imported_first(self):
        import ticker_analyzer.analysis as analysis
        from ticker_analyzer.analysis.engine import StockAnalysisEngine, analyze_ticker

        self.assertIs(analysis.StockAnalysisEngine, StockAnalysisEngine)
        self.assertIs(analysis.analyze_ticker, analyze_ticker)
        with self.assertRaises(AttributeError):
            _ = analysis.unknown_export

    def setUp(self):
        self.annual = statements([10.], ["2024-12-31"])
        self.quarterly = statements([10., 20., 30., 40.], ["2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"])

    def test_current_and_comparison_use_same_ttm_instead_of_annual_or_reported_pe(self):
        prices = pd.DataFrame({"Close": [10., 11.]}, index=pd.to_datetime(["2025-10-31", "2025-11-30"]))
        balance = statements([100.], ["2024-12-31"], "Ordinary Shares Number")
        context = build_historical_ratio_context(prices, self.annual, balance, pd.DataFrame(), years=1, quarterly_income=self.quarterly)
        info = {"marketCap": 2000.}
        current, note = current_valuation_multiple(info, "pe", context, fallback_current_ratio=72.)
        comparison = statement_aligned_ratio_vs_history_metric(info, "pe", context, fallback_current_ratio=72.)
        self.assertEqual(current, 20.)
        self.assertIn("TTM (4 consecutive quarters) ending 2025-06-30", note)
        self.assertIn("current 20.00x", comparison["note"])
        self.assertAlmostEqual(comparison["value"], (20 / 10.5 - 1) * 100)

    def test_missing_or_nonconsecutive_quarters_cannot_form_ttm(self):
        for quarterly in (
            self.quarterly.iloc[:, :3],
            statements([10., 20., None, 40.], self.quarterly.columns),
            statements([10., 20., 30., 40.], ["2024-03-31", "2024-09-30", "2024-12-31", "2025-03-31"]),
        ):
            with self.subTest(columns=quarterly.columns):
                value, note = valuation_periods(self.annual, quarterly, ["Net Income"]).current()
                self.assertEqual(value, 10.)
                self.assertEqual(note, "Annual fallback ending 2024-12-31")

    def test_newer_annual_report_wins_over_old_ttm(self):
        annual = statements([300.], ["2025-12-31"])
        value, note = valuation_periods(annual, self.quarterly, ["Net Income"]).current()
        self.assertEqual(value, 300.)
        self.assertIn("Annual fallback", note)

    def test_ttm_does_not_become_historically_available_until_all_quarters_are_filed(self):
        self.quarterly.attrs["filed_dates"] = {date: pd.Timestamp("2025-10-01") for date in self.quarterly.columns}
        history = valuation_periods(self.annual, self.quarterly, ["Net Income"]).historical()
        self.assertEqual(history.loc[:"2025-09-30"].iloc[-1], 10.)
        self.assertEqual(history.loc["2025-10-01"], 100.)

    def test_balance_is_latest_snapshot_not_sum_of_quarters(self):
        periods = valuation_periods(self.annual, self.quarterly, ["Net Income"], stock=True)
        self.assertEqual(periods.current(), (40., "Quarterly balance ending 2025-06-30"))

    def test_losses_do_not_fall_back_to_a_positive_provider_pe(self):
        quarterly = self.quarterly * -1
        context = build_historical_ratio_context(pd.DataFrame(), self.annual, pd.DataFrame(), pd.DataFrame(), years=1, quarterly_income=quarterly)
        value, note = current_valuation_multiple({"marketCap": 1000}, "pe", context, fallback_current_ratio=15)
        self.assertIsNone(value)
        self.assertIn("non-positive denominator", note)

    def test_fcf_derivation_preserves_explicit_values_and_requires_full_ttm(self):
        annual = statements([5.], ["2024-12-31"], "Free Cash Flow")
        quarterly = self.quarterly.rename(index={"Net Income": "Operating Cash Flow"})
        quarterly.loc["Capital Expenditure"] = [-1., -2., -3., -4.]
        quarterly.loc["Free Cash Flow"] = [None, 17., None, None]
        value, note = free_cash_flow_periods(annual, quarterly).current()
        self.assertEqual(value, 89.)
        self.assertIn("TTM", note)
        self.assertEqual(quarterly.loc["Free Cash Flow"].isna().sum(), 3)

    def test_no_statement_data_is_explicit_and_can_use_provider_multiple(self):
        periods = valuation_periods(pd.DataFrame(), pd.DataFrame(), ["Net Income"])
        self.assertEqual(periods.current(), (None, "statement period unavailable"))
        self.assertTrue(periods.historical().empty)
        context = build_historical_ratio_context(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), years=1)
        value, note = current_valuation_multiple({}, "pe", context, fallback_current_ratio=15)
        self.assertEqual(value, 15.)
        self.assertIn("provider period", note)
