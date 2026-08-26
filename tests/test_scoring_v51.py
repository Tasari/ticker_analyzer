from __future__ import annotations

import unittest

from scripts.calibrate_scoring import build_before_after_report, calibration_acceptance_checks
from ticker_analyzer.analysis.engine import metric_coverage, overall_score_with_missing_policy
from ticker_analyzer.config import load_config
from ticker_analyzer.domain import MetricResult
from ticker_analyzer.scoring import (
    apply_absolute_guardrail,
    calculate_rating_decision,
    percentile_score,
    score_value,
)


class ScoringV51Test(unittest.TestCase):
    def setUp(self):
        self.tabs = {"Growth": 75.0, "Fundamentals": 75.0, "Value": 75.0}

    def test_data_quality_bands_control_confidence_and_caps(self):
        insufficient = calculate_rating_decision(85, 39.9, self.tabs, {})
        low = calculate_rating_decision(85, 54.9, self.tabs, {})
        medium = calculate_rating_decision(85, 64.9, self.tabs, {})
        high = calculate_rating_decision(85, 65, self.tabs, {})
        self.assertEqual(insufficient["rating_code"], "insufficient_data")
        self.assertEqual((low["rating_code"], low["rating_confidence"]), ("neutral", "Low"))
        self.assertEqual((medium["rating_code"], medium["rating_confidence"]), ("strong", "Medium"))
        self.assertEqual((high["rating_code"], high["rating_confidence"]), ("very_strong", "High"))

    def test_fundamentals_and_two_tabs_are_required(self):
        missing_fundamentals = calculate_rating_decision(
            70, 70, {"Growth": 70, "Fundamentals": None, "Value": 70}, {}
        )
        two_tabs = calculate_rating_decision(
            70, 70, {"Growth": 70, "Fundamentals": 70, "Value": None}, {}
        )
        self.assertEqual(missing_fundamentals["rating_code"], "insufficient_data")
        self.assertEqual(two_tabs["rating_code"], "strong")

    def test_generic_financial_model_caps_at_buy(self):
        decision = calculate_rating_decision(
            90, 80, self.tabs, {}, model_applicability=65, profile_rating_cap="strong"
        )
        self.assertEqual(decision["rating_code"], "strong")
        self.assertIn("profile_model_cap", decision["rating_caps"])

    def test_overall_requires_fundamentals_and_applies_one_missing_penalty(self):
        config = {
            "tab_weights": {"Growth": 0.3, "Fundamentals": 0.4, "Value": 0.3},
            "missing_policy": {
                "minimum_scored_tabs": 2,
                "required_tabs": ["Fundamentals"],
                "missing_tab_penalty": {"1": 5},
            },
        }
        self.assertEqual(
            overall_score_with_missing_policy(
                {"Growth": {"score": 70}, "Fundamentals": {"score": 70}, "Value": {"score": None}},
                config,
            ),
            65,
        )
        self.assertIsNone(
            overall_score_with_missing_policy(
                {"Growth": {"score": 70}, "Fundamentals": {"score": None}, "Value": {"score": 70}},
                config,
            )
        )

    def test_percentile_anchors_and_absolute_guardrails(self):
        self.assertEqual(percentile_score(0.5), 50)
        self.assertEqual(percentile_score(0.9), 85)
        config = {"absolute_guardrails": {"roic": [{"at_or_below": 0, "maximum_score": 30}]}}
        self.assertEqual(apply_absolute_guardrail("roic", -2, 70, config), 30)

    def test_zero_weight_group_does_not_affect_coverage(self):
        metrics = [
            MetricResult("used", "Used", 1, "", 50, 1, "Watch"),
            MetricResult("peer", "Peer", None, "", None, 1, "Missing"),
        ]
        config = {"tab_groups": {"Value": {
            "used": {"weight": 1, "metrics": ["used"]},
            "peer": {"weight": 0, "metrics": ["peer"]},
        }}}
        self.assertEqual(metric_coverage(metrics, "Value", config)["percentage"], 100)

    def test_absolute_value_anchors_do_not_saturate_at_plausible_extremes(self):
        config = load_config()
        value_metrics = {metric["id"]: metric for metric in config["metrics"]["Value"]}

        self.assertLess(score_value(0, value_metrics["pe_current"]), 100)
        self.assertLess(score_value(0, value_metrics["ev_ebitda_current"]), 100)
        self.assertEqual(score_value(-100, value_metrics["pe_vs_selected_median"]), 100)

    def test_calibration_report_contains_checks_and_before_after_reasons(self):
        result = {
            "ticker": "AAA",
            "profile": "Industrial",
            "overall_score": 70,
            "data_quality": 70,
            "rating": "Buy",
            "rating_code": "strong",
            "rating_reason_codes": ["base_rating_strong"],
            "tabs": {name: {"score": 70, "coverage": {"percentage": 80}} for name in self.tabs},
        }
        self.assertIn("median_data_quality_65_to_80", calibration_acceptance_checks([result]))
        comparison = build_before_after_report([{**result, "overall_score": 60}], [result])
        self.assertEqual(comparison[0]["delta"]["overall_score"], 10)
        self.assertEqual(comparison[0]["reason_for_delta"], ["base_rating_strong"])


if __name__ == "__main__":
    unittest.main()
