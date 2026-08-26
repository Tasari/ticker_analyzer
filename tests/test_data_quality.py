from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.scoring.quality import calculate_data_quality, freshness_score, observation_depth_score


class DataQualityTest(unittest.TestCase):
    def test_quarterly_filing_drives_freshness(self):
        now = pd.Timestamp("2026-07-31", tz="UTC")
        self.assertEqual(freshness_score(pd.Timestamp("2026-07-25", tz="UTC"), now), 100)

    def test_actual_observations_helper_remains_compatible(self):
        self.assertEqual(observation_depth_score(12, 12), 100)
        self.assertEqual(observation_depth_score(6, 12), 50)

    def test_missing_reconciliation_is_removed_and_weights_are_renormalized(self):
        score, breakdown = quality_fixture(reconciliation_score=None, source_mix="primary_only")
        self.assertEqual(score, 85)
        self.assertIsNone(breakdown["components"]["cross_source_reconciliation"])
        self.assertNotIn("cross_source_reconciliation", breakdown["normalized_weights"])
        self.assertAlmostEqual(sum(breakdown["normalized_weights"].values()), 1)

    def test_source_mix_caps(self):
        secondary, _ = quality_fixture(source_mix="secondary_only")
        primary, _ = quality_fixture(source_mix="primary_only")
        mixed, _ = quality_fixture(source_mix="primary_and_secondary")
        self.assertEqual((secondary, primary, mixed), (75, 85, 92))

    def test_generic_financial_and_incomplete_tab_do_not_cap_dq(self):
        baseline, _ = quality_fixture(source_mix="primary_and_secondary")
        generic, breakdown = quality_fixture(
            source_mix="primary_and_secondary", generic_financial=True, complete_tabs=2, total_tabs=3
        )
        self.assertEqual(generic, baseline)
        self.assertNotIn("generic_financial", breakdown["caps"])
        self.assertNotIn("incomplete_tab", breakdown["caps"])

    def test_provider_errors_do_not_double_penalize_quality(self):
        clean, _ = quality_fixture(provider_errors=0)
        degraded, breakdown = quality_fixture(provider_errors=2)
        self.assertEqual(clean, degraded)
        self.assertEqual(breakdown["penalties"], {})

    def test_period_and_source_mismatches_retain_safety_cap(self):
        period, period_breakdown = quality_fixture(has_period_mismatch=True)
        source, source_breakdown = quality_fixture(has_critical_mismatch=True, reconciliation_score=70)
        self.assertEqual(period, 55)
        self.assertEqual(source, 55)
        self.assertIn("critical_period_mismatch", period_breakdown["caps"])
        self.assertIn("critical_source_mismatch", source_breakdown["caps"])

    def test_freshness_bands_timezone_normalization_and_missing_dates(self):
        now = pd.Timestamp("2026-12-31", tz="UTC")
        expected = {
            0: 100,
            46: 90,
            91: 75,
            136: 60,
            181: 40,
            271: 20,
            366: 5,
        }
        self.assertEqual(freshness_score(None, now), 0)
        self.assertEqual(freshness_score(pd.NaT, now), 0)
        for days, score in expected.items():
            with self.subTest(days=days):
                naive_filing = (now - pd.Timedelta(days=days)).tz_localize(None)
                self.assertEqual(freshness_score(naive_filing, now.tz_localize(None)), score)
        self.assertEqual(freshness_score(now + pd.Timedelta(days=1), now), 100)

    def test_depth_and_quality_values_are_bounded(self):
        self.assertEqual(observation_depth_score(10, 0), 0)
        self.assertEqual(observation_depth_score(-1, 10), 0)
        self.assertEqual(observation_depth_score(20, 10), 100)
        score, breakdown = calculate_data_quality(
            metric_weight_coverage=200,
            filing_freshness=-10,
            provenance_score=50,
            reconciliation_score=None,
            source_mix="custom",
            config={"data_quality": {"component_weights": {
                "effective_metric_coverage": 0,
                "data_freshness": 0,
                "source_quality": 0,
                "cross_source_reconciliation": 0,
            }}},
        )
        self.assertEqual(score, 0)
        self.assertEqual(breakdown["normalized_weights"], {})
        self.assertEqual(breakdown["caps"], [])


def quality_fixture(**overrides):
    values = {
        "metric_weight_coverage": 100,
        "filing_freshness": 100,
        "provenance_score": 100,
        "reconciliation_score": 100,
        "source_mix": "secondary_only",
        "has_period_mismatch": False,
        "has_critical_mismatch": False,
    }
    values.update(overrides)
    return calculate_data_quality(**values)


if __name__ == "__main__":
    unittest.main()
