from __future__ import annotations

import json
import unittest
from copy import deepcopy

import pandas as pd
from ticker_analyzer.metrics.valuation import (
    build_historical_ratio_context,
    current_valuation_multiple,
    valuation_details,
)
from ticker_analyzer.ranking.builder import analysis_fingerprint
from ticker_analyzer.ranking.quality import ranking_compatibility


def context(*, debt=0., cash=0., earnings=10.):
    date = pd.Timestamp("2024-12-31")
    income = pd.DataFrame({date: [earnings, earnings]}, index=["Net Income", "EBITDA"])
    balance = pd.DataFrame({date: [10., debt, cash]}, index=["Ordinary Shares Number", "Total Debt", "Cash And Cash Equivalents"])
    history = pd.DataFrame({"Close": [10.] * 12}, index=pd.date_range("2025-01-31", periods=12, freq="ME"))
    return build_historical_ratio_context(history, income, balance, pd.DataFrame(), years=1)


class ValuationEvidenceTests(unittest.TestCase):
    def test_zero_debt_and_cash_are_valid_but_missing_components_are_not_zero(self):
        valid = context()
        self.assertEqual(valid.statement_aligned_current_ratio("ev_ebitda", {"marketCap": 100}), 10.)
        self.assertTrue(valid.historical_ratios("ev_ebitda"))
        for incomplete in (context(debt=None), context(cash=None)):
            self.assertIsNone(incomplete.statement_aligned_current_ratio("ev_ebitda", {"marketCap": 100}))
            self.assertEqual(incomplete.historical_ratios("ev_ebitda"), [])
            current, note = current_valuation_multiple({"marketCap": 100}, "ev_ebitda", incomplete, fallback_current_ratio=12)
            self.assertEqual(current, 12)
            self.assertIn("not assumed zero", note)
            details = valuation_details({"marketCap": 100}, "ev_ebitda", incomplete, 12)
            self.assertEqual(details["source"], "Yahoo fallback")
            self.assertEqual(details["period"], "Unverified")
            self.assertIsNone(details["difference_pct"])
            self.assertTrue(any("Debt or cash" in warning for warning in details["warnings"]))

    def test_negative_ebitda_and_negative_ev_do_not_create_positive_historical_ratios(self):
        ctx = context(cash=200., earnings=-10.)
        self.assertEqual(ctx.historical_ratios("ev_ebitda"), [])
        self.assertIsNone(ctx.statement_aligned_current_ratio("ev_ebitda", {"marketCap": 100}))

    def test_comparison_is_structured_serializable_and_flags_large_differences(self):
        details = valuation_details({"marketCap": 100}, "pe", context(), 20)
        self.assertEqual(details["source"], "Statements")
        self.assertEqual(details["period"], "Annual fallback ending 2024-12-31")
        self.assertEqual(details["difference_pct"], -50.)
        self.assertEqual(details["history_expected"], 12)
        self.assertEqual(details["history_observations"], 10)
        self.assertTrue(any("25%" in warning for warning in details["warnings"]))
        json.dumps(details, allow_nan=False)

    def test_small_discrepancy_does_not_warn_and_history_cache_is_not_mutable_by_caller(self):
        ctx = context()
        details = valuation_details({"marketCap": 100}, "pe", ctx, 10.5)
        self.assertEqual(details["warnings"], [])
        values = ctx.historical_ratios("pe")
        values.clear()
        self.assertEqual(len(ctx.historical_ratios("pe")), 10)

    def test_missing_and_non_positive_earnings_are_explicit(self):
        for earnings in (None, 0., -10.):
            details = valuation_details({"marketCap": 100}, "pe", context(earnings=earnings), None)
            self.assertEqual(details["source"], "Unavailable")
            self.assertIsNone(details["difference_pct"])
            self.assertTrue(any("half" in warning for warning in details["warnings"]))
            if earnings is not None:
                self.assertTrue(any("Non-positive" in warning for warning in details["warnings"]))


class RankingCompatibilityTests(unittest.TestCase):
    def test_same_calculations_on_an_older_date_remain_compatible(self):
        config = {"version": 5}
        fingerprint = analysis_fingerprint(config, "2020-01-01")
        payload = {"metadata": fingerprint, "companies": [{"ticker": "ABC", **fingerprint}]}
        original = deepcopy(payload)
        self.assertTrue(ranking_compatibility(payload, config)["compatible"])
        self.assertEqual(payload, original)

    def test_old_and_unknown_versions_and_changed_configuration_are_explained(self):
        config = {"version": 5}
        fingerprint = analysis_fingerprint(config, "2026-01-01")
        for metadata in ({}, {**fingerprint, "metric_schema_version": "old"}, {**fingerprint, "config_digest": "different"}):
            result = ranking_compatibility({"metadata": metadata}, config)
            self.assertFalse(result["compatible"])
            self.assertTrue(result["differences"])
            self.assertTrue(result["warnings"])

    def test_mixed_row_versions_are_detected_even_with_current_metadata(self):
        config = {"version": 5}
        fingerprint = analysis_fingerprint(config, "2026-01-01")
        result = ranking_compatibility({"metadata": fingerprint, "companies": [
            {"ticker": "ABC", "metric_schema_version": "old"}, {"ticker": "DEF"},
        ]}, config)
        self.assertEqual(result["incompatible_rows"], 1)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["differences"], [])
        self.assertIn("1 rows", result["warnings"][0])
