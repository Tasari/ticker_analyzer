from __future__ import annotations

import math
import unittest

from ticker_analyzer.analysis.engine import overall_score_with_missing_policy
from ticker_analyzer.scoring import (
    RATING_RANK,
    ScoringEngine,
    calculate_overall_rating,
    calculate_overall_rating_code,
    cap_rating,
    classify_rating,
    classify_tab_rating,
    format_metric_value,
    format_threshold,
    score_higher,
    score_lower,
    score_value,
    weighted_tab_score,
)


class ScoringV3Test(unittest.TestCase):
    def test_formatters_and_missing_classifiers(self):
        self.assertEqual(format_metric_value(None, "%"), "Missing")
        self.assertEqual(format_metric_value(1.25, "%"), "1.25%")
        self.assertEqual(format_metric_value(1.25, "x"), "1.25x")
        self.assertEqual(format_metric_value(1.25, "$B"), "$1.25B")
        self.assertEqual(format_metric_value(1.25, "pp"), "1.25 pp")
        self.assertEqual(format_metric_value(1.25, ""), "1.25")
        self.assertEqual(format_threshold(None, "%"), "not set")
        self.assertEqual(format_threshold(2, "$B"), "$2B")
        self.assertEqual(format_threshold(2, "pp"), "2 pp")
        self.assertEqual(classify_rating(None, {}), "Not Rated")
        self.assertEqual(classify_tab_rating("Growth", None, {}), "Not Rated")

    def test_invalid_anchor_and_direction_validation(self):
        with self.assertRaises(ValueError):
            score_higher(1, 2, 2)
        with self.assertRaises(ValueError):
            score_lower(1, 2, 2)
        with self.assertRaises(ValueError):
            score_value(1, {"warn": None, "good": 2})
        with self.assertRaises(ValueError):
            score_value(1, {"warn": 0, "good": 2, "direction": "sideways"})

    def test_scoring_engine_missing_metric_and_empty_tab_weight(self):
        engine = ScoringEngine()
        metric = {"id": "m", "name": "Metric", "weight": 1, "warn": 0, "good": 10, "unit": "%"}
        missing = engine.score_metric(metric, {}, "Growth", {})
        present = engine.score_metric(metric, {"m": {"value": 5}}, "Growth", {})
        self.assertIsNone(missing.score)
        self.assertEqual(present.score, 50)
        self.assertIsNone(weighted_tab_score({}, {}))
        self.assertEqual(engine.classify_rating(50, {}), "Hold")
        self.assertEqual(cap_rating("Buy", "Hold"), "Hold")
        self.assertEqual(cap_rating("Sell", "Hold"), "Sell")
    def test_higher_anchor_points(self):
        self.assertEqual([score_higher(value, 0, 10) for value in (-10, 0, 5, 10, 20)], [0, 25, 50, 75, 100])

    def test_lower_anchor_points(self):
        self.assertEqual([score_lower(value, 30, -30) for value in (90, 30, 0, -30, -90)], [0, 25, 50, 75, 100])

    def test_scoring_is_monotonic(self):
        values = list(range(-100, 101))
        higher = [score_higher(value, 0, 10) for value in values]
        lower = [score_lower(value, 30, -30) for value in values]
        self.assertTrue(all(left <= right for left, right in zip(higher, higher[1:], strict=False)))
        self.assertTrue(all(left >= right for left, right in zip(lower, lower[1:], strict=False)))

    def test_non_finite_values_fail(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                score_higher(value, 0, 10)

    def test_price_target_ten_percent_is_neutral(self):
        self.assertEqual(score_higher(10, -10, 30), 50)

    def test_overall_penalizes_weakest_tab(self):
        tabs = {"Growth": {"score": 90}, "Fundamentals": {"score": 30}, "Value": {"score": 95}}
        config = {
            "tab_weights": {"Growth": 0.3333, "Fundamentals": 0.3333, "Value": 0.3334},
            "missing_policy": {"require_all_tabs_for_overall": True, "minimum_scored_tabs": 3},
        }
        self.assertAlmostEqual(overall_score_with_missing_policy(tabs, config), 69.669, places=3)

    def test_rating_gates_block_strong_buy(self):
        tabs = {"Growth": 95.0, "Fundamentals": 30.0, "Value": 95.0}
        self.assertNotEqual(calculate_overall_rating(88, 90, tabs, {}), "Strong Buy")
        self.assertEqual(
            calculate_overall_rating(88, 30, {key: 90.0 for key in tabs}, {}),
            "Insufficient Data",
        )

    def test_missing_tab_can_still_receive_rating(self):
        self.assertEqual(
            calculate_overall_rating(80, 90, {"Growth": 80, "Fundamentals": 80, "Value": None}, {}),
            "Strong Buy",
        )

    def test_rating_caps_never_upgrade_bearish_rating(self):
        tabs = {"Growth": 20.0, "Fundamentals": 20.0, "Value": 20.0}
        self.assertEqual(calculate_overall_rating(20, 20, tabs, {}), "Insufficient Data")

    def test_rating_is_monotonic_in_overall_when_gates_are_equal(self):
        tabs = {"Growth": 80.0, "Fundamentals": 80.0, "Value": 80.0}
        ratings = [calculate_overall_rating(overall, 90, tabs, {}) for overall in range(101)]
        ranks = [RATING_RANK[rating] for rating in ratings]
        self.assertTrue(all(left <= right for left, right in zip(ranks, ranks[1:], strict=False)))

    def test_custom_labels_do_not_change_semantic_gates(self):
        config = {
            "overall_rating_labels": {"very_strong": "Top", "strong": "Good", "neutral": "Wait"},
            "rating_gates": {"minimum_data_quality_for_directional_rating": 60},
        }
        tabs = {"Growth": 90.0, "Fundamentals": 90.0, "Value": 90.0}
        self.assertEqual(calculate_overall_rating_code(90, 90, tabs, config), "very_strong")
        self.assertEqual(calculate_overall_rating(90, 90, tabs, config), "Top")


if __name__ == "__main__":
    unittest.main()
