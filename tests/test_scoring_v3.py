from __future__ import annotations

import math
import unittest

from ticker_analyzer.analysis.engine import overall_score_with_missing_policy
from ticker_analyzer.scoring import RATING_RANK, calculate_overall_rating, score_higher, score_lower


class ScoringV3Test(unittest.TestCase):
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
        self.assertAlmostEqual(overall_score_with_missing_policy(tabs, config), 63.3353, places=3)

    def test_rating_gates_block_strong_buy(self):
        tabs = {"Growth": 95.0, "Fundamentals": 30.0, "Value": 95.0}
        self.assertNotEqual(calculate_overall_rating(88, 90, tabs, {}), "Strong Buy")
        self.assertEqual(calculate_overall_rating(88, 30, {key: 90.0 for key in tabs}, {}), "Hold")

    def test_missing_tab_is_insufficient_data(self):
        self.assertEqual(
            calculate_overall_rating(80, 90, {"Growth": 80, "Fundamentals": 80, "Value": None}, {}),
            "Insufficient Data",
        )

    def test_rating_caps_never_upgrade_bearish_rating(self):
        tabs = {"Growth": 20.0, "Fundamentals": 20.0, "Value": 20.0}
        self.assertEqual(calculate_overall_rating(20, 20, tabs, {}), "Strong Sell")

    def test_rating_is_monotonic_in_overall_when_gates_are_equal(self):
        tabs = {"Growth": 80.0, "Fundamentals": 80.0, "Value": 80.0}
        ratings = [calculate_overall_rating(overall, 90, tabs, {}) for overall in range(101)]
        ranks = [RATING_RANK[rating] for rating in ratings]
        self.assertTrue(all(left <= right for left, right in zip(ranks, ranks[1:], strict=False)))


if __name__ == "__main__":
    unittest.main()
