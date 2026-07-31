from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_config import migrate_file
from ticker_analyzer.config import ConfigValidationError, load_config, normalize_config, save_config


class ConfigV4Test(unittest.TestCase):
    def test_repository_config_is_v4_and_expands_specialized_profiles(self):
        config = load_config()
        self.assertEqual(config["version"], 5)
        self.assertEqual(config["scoring_model"], "piecewise_anchor_25_75_v1")
        self.assertIn("FinancialBank", config["profile_metrics"])
        self.assertIn("FinancialBroker", config["profile_rules"])

    def test_v2_is_rejected_with_explicit_migration_message(self):
        with self.assertRaisesRegex(ConfigValidationError, "v2"):
            normalize_config({"version": 2})

    def test_group_reference_validation_rejects_unknown_metric(self):
        config = load_config()
        config["tab_groups"]["Growth"]["historical"]["metrics"].append("not_a_metric")
        with self.assertRaisesRegex(ConfigValidationError, "unknown metric"):
            normalize_config(config)

    def test_positive_weight_metric_must_belong_to_exactly_one_group(self):
        config = load_config()
        config["tab_groups"]["Fundamentals"]["solvency"]["metrics"].remove("quick_ratio")
        with self.assertRaisesRegex(ConfigValidationError, "omits positive-weight"):
            normalize_config(config)
        config = load_config()
        config["tab_groups"]["Fundamentals"]["quality"]["metrics"].append("quick_ratio")
        with self.assertRaisesRegex(ConfigValidationError, "duplicate group membership"):
            normalize_config(config)

    def test_v5_missing_policies_receive_v51_defaults(self):
        config = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        config.pop("missing_policy")
        config.pop("minimum_weight_coverage")
        normalized = normalize_config(config)
        self.assertFalse(normalized["missing_policy"]["require_all_tabs_for_overall"])
        self.assertEqual(normalized["missing_policy"]["required_tabs"], ["Fundamentals"])
        self.assertEqual(normalized["minimum_weight_coverage"]["Fundamentals"], 0.60)

    def test_v51_config_remains_compatible_with_stale_v5_validator(self):
        raw = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        expected_legacy = {
            "metric_weight_coverage", "tab_completeness", "filing_freshness",
            "actual_observation_depth", "source_provenance", "cross_source_reconciliation",
            "temporal_alignment", "estimate_quality", "profile_fit",
        }
        self.assertEqual(set(raw["data_quality"]["weights"]), expected_legacy)
        self.assertAlmostEqual(sum(raw["data_quality"]["weights"].values()), 1)
        self.assertEqual(set(raw["data_quality"]["component_weights"]), {
            "effective_metric_coverage", "data_freshness", "source_quality",
            "cross_source_reconciliation",
        })

    def test_early_v51_weight_schema_is_migrated_to_rolling_safe_schema(self):
        config = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        active_weights = config["data_quality"].pop("component_weights")
        config["data_quality"]["weights"] = active_weights
        normalized = normalize_config(config)
        self.assertEqual(normalized["data_quality"]["component_weights"], active_weights)
        self.assertIn("profile_fit", normalized["data_quality"]["weights"])

    def test_save_config_round_trips_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "nested" / "config.json"
            save_config(load_config(), target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 5)

    def test_in_place_migration_creates_timestamped_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "config.json"
            payload = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
            payload["version"] = 4
            target.write_text(json.dumps(payload), encoding="utf-8")
            migrate_file(target)
            self.assertTrue(list(target.parent.glob("config.json.v4.*.bak")))

    def test_migration_writes_v4_to_separate_destination(self):
        source_payload = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        source_payload["version"] = 3
        source_payload.pop("scoring_model", None)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "v3.json"
            output = Path(temporary) / "v4.json"
            source.write_text(json.dumps(source_payload), encoding="utf-8")
            migrate_file(source, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["version"], 5)

    def test_v3_migration_removes_financial_metric_from_industrial_group(self):
        config = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        config["version"] = 3
        config["tab_groups"]["Value"]["historical_multiples"]["metrics"].append("pb_vs_selected_median")
        migrated = normalize_config(config)
        self.assertNotIn(
            "pb_vs_selected_median",
            migrated["tab_groups"]["Value"]["historical_multiples"]["metrics"],
        )


if __name__ == "__main__":
    unittest.main()
