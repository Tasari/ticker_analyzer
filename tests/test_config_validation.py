from __future__ import annotations

import unittest

from ticker_analyzer.config import (
    ConfigValidationError,
    migrate_metric_ids,
    normalize_config,
    validate_coverage_policy,
    validate_data_quality,
    validate_groups,
    validate_metric,
    validate_minimum_coverage,
    validate_missing_policy,
    validate_profile_metrics,
    validate_profile_rules,
    validate_tab_weights,
    validate_thresholds,
)
from ticker_analyzer.config.defaults import default_data_quality_config


def metric(**overrides):
    value = {
        "id": "m1",
        "name": "Metric",
        "weight": 1.0,
        "direction": "higher",
        "warn": 0,
        "good": 1,
        "unit": "%",
        "description": "Description",
    }
    value.update(overrides)
    return value


class ConfigValidationTest(unittest.TestCase):
    def assert_invalid(self, call, pattern: str):
        with self.assertRaisesRegex(ConfigValidationError, pattern):
            call()

    def test_normalize_rejects_non_objects_and_unknown_versions(self):
        self.assert_invalid(lambda: normalize_config([]), "JSON object")
        self.assert_invalid(lambda: normalize_config({"version": 99}), "Unsupported")

    def test_tab_weight_and_threshold_validation_errors(self):
        cases = [
            (lambda: validate_tab_weights([], {"Growth": []}), "object"),
            (lambda: validate_tab_weights({}, {"Growth": []}), "must be a number"),
            (lambda: validate_tab_weights({"Growth": -1}, {"Growth": []}), "negative"),
            (lambda: validate_tab_weights({"Growth": 0.5}, {"Growth": []}), "sum"),
            (lambda: validate_thresholds([], "thresholds"), "object"),
            (lambda: validate_thresholds({}, "thresholds"), "must define numeric"),
            (
                lambda: validate_thresholds(
                    {"very_strong": 80, "strong": 90, "neutral": 50, "weak": 20},
                    "thresholds",
                ),
                "descending",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_metric_validation_errors(self):
        cases = [
            (lambda: validate_metric([], "metric"), "object"),
            (lambda: validate_metric({"id": "x"}, "metric"), "missing required"),
            (lambda: validate_metric(metric(id=" "), "metric"), "cannot be empty"),
            (lambda: validate_metric(metric(direction="sideways"), "metric"), "higher or lower"),
            (lambda: validate_metric(metric(weight=-1), "metric"), "non-negative"),
            (lambda: validate_metric(metric(warn=None), "metric"), "must be numbers"),
            (lambda: validate_metric(metric(warn=1, good=1), "metric"), "cannot be equal"),
            (lambda: validate_metric(metric(warn=2, good=1), "metric"), "greater than warn"),
            (
                lambda: validate_metric(metric(direction="lower", warn=1, good=2), "metric"),
                "lower than warn",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_missing_and_coverage_policy_validation_errors(self):
        tabs = {"Growth": [metric()]}
        cases = [
            (lambda: validate_missing_policy([]), "object"),
            (
                lambda: validate_missing_policy({"require_all_tabs_for_overall": "yes"}),
                "true or false",
            ),
            (lambda: validate_missing_policy({"minimum_scored_tabs": 0}), "at least 1"),
            (lambda: validate_missing_policy({"required_tabs": "Growth"}), "list"),
            (lambda: validate_missing_policy({"missing_tab_penalty": {"1": -1}}), "non-negative"),
            (lambda: validate_minimum_coverage([], tabs), "object"),
            (lambda: validate_minimum_coverage({"Growth": 2}, tabs), "between 0 and 1"),
            (lambda: validate_coverage_policy([], tabs), "object"),
            (lambda: validate_coverage_policy({}, tabs), "must be an object"),
            (
                lambda: validate_coverage_policy(
                    {"minimum_to_score": {"Growth": 2}, "minimum_for_full_confidence": {"Growth": 1}},
                    tabs,
                ),
                "between 0 and 1",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_profile_metric_and_group_validation_errors(self):
        cases = [
            (lambda: validate_profile_metrics([]), "object"),
            (lambda: validate_profile_metrics({"Bank": {}}), "non-empty object"),
            (lambda: validate_profile_metrics({"Bank": {"Growth": []}}), "non-empty list"),
            (
                lambda: validate_profile_metrics({"Bank": {"Growth": [metric(), metric()]}}),
                "duplicate",
            ),
            (lambda: validate_groups([], {"m1"}, "groups"), "non-empty object"),
            (lambda: validate_groups({"g": []}, {"m1"}, "groups"), "must be an object"),
            (
                lambda: validate_groups({"g": {"weight": -1, "metrics": ["m1"]}}, {"m1"}, "groups"),
                "weight is invalid",
            ),
            (
                lambda: validate_groups({"g": {"weight": 1, "metrics": []}}, {"m1"}, "groups"),
                "non-empty list",
            ),
            (
                lambda: validate_groups({"g": {"weight": 1, "metrics": ["bad"]}}, {"m1"}, "groups"),
                "unknown metric",
            ),
            (
                lambda: validate_groups({"g": {"weight": 0.5, "metrics": ["m1"]}}, {"m1"}, "groups"),
                "sum to 1.0",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_profile_rule_validation_errors(self):
        base = {"tab_groups": {"Growth": {"g": {}}}}
        cases = [
            (lambda: validate_profile_rules({"profile_rules": []}), "must be an object"),
            (
                lambda: validate_profile_rules({"profile_rules": {"Bank": []}}),
                "Bank must be an object",
            ),
            (
                lambda: validate_profile_rules(
                    {**base, "profile_rules": {"Bank": {"Growth": {"minimum_coverage": 2}}}}
                ),
                "must be 0..1",
            ),
            (
                lambda: validate_profile_rules(
                    {
                        **base,
                        "profile_rules": {
                            "Bank": {
                                "Growth": {
                                    "minimum_coverage": 0.5,
                                    "required_groups": {"bad": {}},
                                }
                            }
                        },
                    }
                ),
                "unknown group",
            ),
            (
                lambda: validate_profile_rules(
                    {
                        **base,
                        "profile_rules": {
                            "Bank": {
                                "Growth": {
                                    "minimum_coverage": 0.5,
                                    "required_groups": {"g": {"minimum_available_metrics": -1}},
                                }
                            }
                        },
                    }
                ),
                "minimum is invalid",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_data_quality_schema_validation_errors(self):
        defaults = default_data_quality_config()
        cases = [
            (lambda: validate_data_quality([]), "weights must be an object"),
            (
                lambda: validate_data_quality({"weights": defaults["weights"], "component_weights": []}),
                "component_weights must be an object",
            ),
            (
                lambda: validate_data_quality({"weights": defaults["weights"], "component_weights": {}}),
                "define exactly",
            ),
            (
                lambda: validate_data_quality(
                    {
                        "weights": defaults["weights"],
                        "component_weights": {**defaults["component_weights"], "source_quality": -1},
                    }
                ),
                "non-negative",
            ),
            (
                lambda: validate_data_quality(
                    {
                        "weights": defaults["weights"],
                        "component_weights": {name: 1 for name in defaults["component_weights"]},
                    }
                ),
                "sum to 1.0",
            ),
            (
                lambda: validate_data_quality(
                    {"weights": {}, "component_weights": defaults["component_weights"]}
                ),
                "weights must define exactly",
            ),
            (
                lambda: validate_data_quality(
                    {
                        "weights": {**defaults["weights"], "profile_fit": -1},
                        "component_weights": defaults["component_weights"],
                    }
                ),
                "legacy weights must be non-negative",
            ),
            (
                lambda: validate_data_quality(
                    {
                        "weights": {name: 1 for name in defaults["weights"]},
                        "component_weights": defaults["component_weights"],
                    }
                ),
                "legacy weights must sum",
            ),
        ]
        for call, pattern in cases:
            with self.subTest(pattern=pattern):
                self.assert_invalid(call, pattern)

    def test_metric_id_migration_ignores_unsupported_shapes(self):
        migrate_metric_ids([])
        payload = {"Growth": "not-a-list", "Value": ["not-a-dict", {"id": "pe_vs_3y_median"}]}
        migrate_metric_ids(payload)
        self.assertEqual(payload["Value"][1]["id"], "pe_vs_selected_median")


if __name__ == "__main__":
    unittest.main()
