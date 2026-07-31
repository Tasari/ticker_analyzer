from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.data_quality import calculate_data_quality, freshness_score, observation_depth_score


class DataQualityTest(unittest.TestCase):
    def test_quarterly_filing_drives_freshness(self):
        now = pd.Timestamp("2026-07-31", tz="UTC")
        self.assertEqual(freshness_score(pd.Timestamp("2026-07-25", tz="UTC"), now), 100)

    def test_actual_observations_define_history_quality(self):
        self.assertEqual(observation_depth_score(12, 12), 100)
        self.assertEqual(observation_depth_score(6, 12), 50)

    def test_generic_financial_cap(self):
        score, breakdown = quality_fixture(generic_financial=True, yfinance_only=False)
        self.assertEqual(score, 60)
        self.assertIn("generic_financial", breakdown["caps"])

    def test_provider_errors_apply_explicit_penalty(self):
        clean, _ = quality_fixture(provider_errors=0, yfinance_only=False)
        degraded, breakdown = quality_fixture(provider_errors=2, yfinance_only=False)
        self.assertEqual(clean - degraded, 6)
        self.assertEqual(breakdown["penalties"]["provider_errors"], 6)

    def test_incomplete_tab_caps_quality(self):
        score, breakdown = quality_fixture(complete_tabs=2, yfinance_only=False)
        self.assertEqual(score, 55)
        self.assertIn("incomplete_tab", breakdown["caps"])

    def test_yfinance_production_cap_is_lower_than_local(self):
        local, _ = quality_fixture(yfinance_only=True, production_mode=False)
        production, breakdown = quality_fixture(yfinance_only=True, production_mode=True)
        self.assertEqual(local, 85)
        self.assertEqual(production, 70)
        self.assertIn("yfinance_only", breakdown["caps"])

    def test_period_mismatch_prevents_directional_quality(self):
        score, breakdown = quality_fixture(has_period_mismatch=True, yfinance_only=False)
        self.assertLess(score, 60)
        self.assertIn("critical_period_mismatch", breakdown["caps"])

    def test_critical_source_mismatch_prevents_directional_quality(self):
        score, breakdown = quality_fixture(
            yfinance_only=False, reconciliation_score=70, has_critical_mismatch=True
        )
        self.assertLess(score, 60)
        self.assertIn("critical_source_mismatch", breakdown["caps"])

    def test_maximum_quality_requires_uncapped_primary_quality_inputs(self):
        score, breakdown = quality_fixture(yfinance_only=False)
        self.assertEqual(score, 95)
        self.assertEqual(breakdown["maximum"], 95)


def quality_fixture(**overrides):
    values = {
        "metric_weight_coverage": 100,
        "complete_tabs": 3,
        "total_tabs": 3,
        "filing_freshness": 100,
        "observation_depth": 100,
        "provenance_score": 100,
        "estimate_quality": 100,
        "profile_fit": 100,
        "provider_errors": 0,
        "secondary_fraction": 0,
        "estimated_fraction": 0,
        "has_period_mismatch": False,
        "yfinance_only": True,
        "generic_financial": False,
    }
    values.update(overrides)
    return calculate_data_quality(**values)


if __name__ == "__main__":
    unittest.main()
