from __future__ import annotations

import unittest

from ticker_analyzer.analysis.explanations import analysis_insights, rating_constraints
from ticker_analyzer.domain import MetricResult


class AnalysisExplanationTest(unittest.TestCase):
    def test_insights_rank_signals_and_include_improvement_actions(self):
        result = {
            "tabs": {
                "Growth": {
                    "metrics": [
                        MetricResult("growth", "Revenue Growth", 20, "%", 90, 1.2, "Excellent"),
                        MetricResult("estimate", "EPS Estimate", None, "%", None, 0.5, "Missing"),
                    ]
                },
                "Value": {
                    "metrics": [
                        MetricResult("pe", "Current P/E", 35, "x", 20, 1.0, "Very Expensive"),
                    ]
                },
            },
            "rating_reason_codes": ["buy_gate_failed"],
        }

        insights = analysis_insights(result)

        self.assertIn("Revenue Growth", insights["strongest"][0])
        self.assertIn("Current P/E", insights["weakest"][0])
        self.assertTrue(any("Improve Value · Current P/E" in item for item in insights["improvements"]))
        self.assertTrue(any("Buy gate" in item for item in insights["improvements"]))

    def test_rating_constraints_deduplicate_caps(self):
        result = {
            "rating_reason_codes": ["profile_model_cap"],
            "rating_caps": ["profile_model_cap"],
        }

        self.assertEqual(rating_constraints(result), ["The generic profile model caps the rating at Buy."])


if __name__ == "__main__":
    unittest.main()
