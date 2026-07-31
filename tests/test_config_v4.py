from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.migrate_config import migrate_file
from ticker_analyzer.config import ConfigValidationError, load_config, normalize_config


class ConfigV4Test(unittest.TestCase):
    def test_repository_config_is_v4_and_expands_specialized_profiles(self):
        config = load_config()
        self.assertEqual(config["version"], 4)
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

    def test_migration_writes_v4_to_separate_destination(self):
        source_payload = json.loads(Path("metrics_config.json").read_text(encoding="utf-8"))
        source_payload["version"] = 3
        source_payload.pop("scoring_model", None)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "v3.json"
            output = Path(temporary) / "v4.json"
            source.write_text(json.dumps(source_payload), encoding="utf-8")
            migrate_file(source, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["version"], 4)

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
