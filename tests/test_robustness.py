from __future__ import annotations

import unittest

from ticker_analyzer.analysis.aggregation import (
    grouped_tab_score,
    metric_coverage,
    overall_score_with_missing_policy,
)
from ticker_analyzer.analysis.profiles import config_for_profile
from ticker_analyzer.config import load_config
from ticker_analyzer.data_quality import calculate_data_quality
from ticker_analyzer.domain import MetricResult
from ticker_analyzer.ratings import calculate_rating_decision
from ticker_analyzer.robustness import (
    EXPERIMENTAL_MISSING_DATA_POLICIES,
    RobustnessAuditError,
    audit_scoring_robustness,
    compact_analysis_result,
    perturb_analysis,
)


def analysis(ticker: str, profile: str, offset: float = 0) -> dict:
    config = load_config()
    scoring_config = config_for_profile(config, profile)
    tabs = {}
    for tab_name, definitions in scoring_config["metrics"].items():
        metrics = [
            MetricResult(
                id=definition["id"],
                name=definition.get("name", definition["id"]),
                value=1,
                unit=definition.get("unit", ""),
                score=min(100, 55 + offset + index),
                weight=float(definition.get("weight", 0)),
                status="Watch",
            )
            for index, definition in enumerate(definitions)
        ]
        tabs[tab_name] = {"metrics": metrics, "score": None, "coverage": {}}
    data_quality, data_quality_breakdown = calculate_data_quality(
        metric_weight_coverage=100,
        filing_freshness=60,
        provenance_score=50,
        reconciliation_score=None,
        source_mix="primary_and_secondary",
        config=scoring_config,
    )
    seed = {
        "ticker": ticker,
        "profile": profile,
        "tabs": tabs,
        "data_quality": data_quality,
        "data_quality_breakdown": data_quality_breakdown,
        "model_applicability": 90,
    }
    for tab_name, tab in tabs.items():
        coverage = metric_coverage(tab["metrics"], tab_name, scoring_config)
        tab["score"], _ = grouped_tab_score(tab_name, tab["metrics"], scoring_config, coverage)
        tab["coverage"] = coverage
    seed["overall_score"] = overall_score_with_missing_policy(tabs, scoring_config)
    seed["rating_code"] = calculate_rating_decision(
        seed["overall_score"],
        seed["data_quality"],
        {name: tab["score"] for name, tab in tabs.items()},
        scoring_config,
        model_applicability=seed["model_applicability"],
        profile_rating_cap=scoring_config.get("active_rating_cap"),
    )["rating_code"]
    return seed


class RobustnessAuditTest(unittest.TestCase):
    def test_audit_is_deterministic_and_segments_financial_companies(self):
        config = load_config()
        results = [
            analysis("AAA", "Industrial", 0),
            analysis("BBB", "Industrial", 8),
            analysis("CCC", "FinancialBroker", 4),
            analysis("DDD", "FinancialBank", 12),
        ]

        first = audit_scoring_robustness(results, config, trials=8, seed=42)
        second = audit_scoring_robustness(results, config, trials=8, seed=42)

        self.assertEqual(first, second)
        self.assertEqual(first["sample"]["industrial"], 2)
        self.assertEqual(first["sample"]["financial"], 2)
        segments = first["dropout_rates"]["10%"]["segments"]
        self.assertEqual(segments["All"]["trials"], 8)
        self.assertEqual(segments["Industrial"]["sample_size"], 2)
        self.assertEqual(segments["Financial"]["sample_size"], 2)
        self.assertIn("most_sensitive_dropped_metrics", first["dropout_rates"]["20%"])
        self.assertEqual(len(first["dropout_rates"]["10%"]["company_stability"]), 4)
        sensitive = first["dropout_rates"]["20%"]["most_sensitive_dropped_metrics"][0]
        self.assertIn("score_unavailable_pct", sensitive)
        self.assertIn("rating_flip_pct", sensitive)
        comparison = first["dropout_rates"]["10%"]["policy_comparison"]
        self.assertEqual(set(comparison), set(EXPERIMENTAL_MISSING_DATA_POLICIES))
        self.assertEqual(segments, comparison["baseline"]["segments"])
        diagnostics = comparison["baseline"]["segments"]["All"]["monotonicity"]
        self.assertIn("score_increase_events", diagnostics)
        self.assertIn("proper_rating_upgrade_events", diagnostics)
        self.assertIn("proper_rating_upgrade_share_pct", diagnostics)
        deltas = first["dropout_rates"]["10%"]["policy_deltas_vs_baseline"]
        self.assertEqual(set(deltas), {"coverage_penalty", "coverage_caps"})
        self.assertIn("proper_rating_upgrade_pct_mean", deltas["coverage_penalty"]["All"])

    def test_perturbation_removes_exact_number_and_recomputes_quality(self):
        config = load_config()
        result = analysis("AAA", "Industrial")
        available = sum(tab["coverage"]["scored_metrics"] for tab in result["tabs"].values())

        changed = perturb_analysis(result, config, dropout_rate=0.2, seed=7)

        self.assertEqual(len(changed.dropped_metrics), round(available * 0.2))
        self.assertLess(changed.data_quality, result["data_quality"])

    def test_experimental_policies_use_the_same_dropout_without_changing_production(self):
        config = load_config()
        result = analysis("AAA", "Industrial", 30)

        baseline = perturb_analysis(result, config, dropout_rate=0.2, seed=7)
        penalty = perturb_analysis(
            result, config, dropout_rate=0.2, seed=7, policy="coverage_penalty"
        )
        capped = perturb_analysis(
            result, config, dropout_rate=0.2, seed=7, policy="coverage_caps"
        )

        self.assertEqual(baseline.dropped_metrics, penalty.dropped_metrics)
        self.assertEqual(baseline.dropped_metrics, capped.dropped_metrics)
        self.assertEqual(baseline.coverage_percentage, penalty.coverage_percentage)
        self.assertLessEqual(penalty.overall_score, baseline.overall_score)
        self.assertEqual(capped.overall_score, baseline.overall_score)

    def test_compact_result_is_json_safe_and_preserves_metrics(self):
        compact = compact_analysis_result(analysis("AAA", "Industrial"))

        self.assertIsInstance(compact["tabs"]["Growth"]["metrics"][0], dict)
        self.assertEqual(compact["ticker"], "AAA")

    def test_rejects_compact_ranking_rows_and_invalid_parameters(self):
        config = load_config()
        with self.assertRaisesRegex(RobustnessAuditError, "At least two"):
            audit_scoring_robustness([analysis("AAA", "Industrial")], config)
        rows = [
            {"ticker": "AAA", "profile": "Industrial", "overall_score": 50, "tabs": {}},
            {"ticker": "BBB", "profile": "Industrial", "overall_score": 60, "tabs": {}},
        ]
        with self.assertRaisesRegex(RobustnessAuditError, "no metric-level"):
            audit_scoring_robustness(rows, config)
        with self.assertRaisesRegex(ValueError, "trials"):
            audit_scoring_robustness(rows, config, trials=0)
        with self.assertRaisesRegex(ValueError, "dropout"):
            audit_scoring_robustness(rows, config, dropout_rates=(1.0,))
        with self.assertRaisesRegex(ValueError, "must include baseline"):
            audit_scoring_robustness(
                [analysis("AAA", "Industrial"), analysis("BBB", "Industrial")],
                config,
                policies=("coverage_penalty",),
            )
        with self.assertRaisesRegex(ValueError, "unknown missing-data policy"):
            perturb_analysis(
                analysis("AAA", "Industrial"),
                config,
                dropout_rate=0.1,
                seed=1,
                policy="imaginary",
            )


if __name__ == "__main__":
    unittest.main()
