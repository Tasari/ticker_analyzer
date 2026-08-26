from __future__ import annotations

from copy import deepcopy
from typing import Any

SCORING_MODEL = "piecewise_anchor_25_75_v1"
CALIBRATION_VERSION = "v5.2-value-2026Q3"
DEFAULT_MINIMUM_COVERAGE = {"Growth": 0.55, "Fundamentals": 0.60, "Value": 0.50}
DEFAULT_FULL_CONFIDENCE_COVERAGE = {"Growth": 0.80, "Fundamentals": 0.80, "Value": 0.75}
DEFAULT_MISSING_POLICY = {
    "require_all_tabs_for_overall": False,
    "minimum_scored_tabs": 2,
    "required_tabs": ["Fundamentals"],
    "missing_tab_penalty": {"0": 0, "1": 5, "2": 15},
}
LEGACY_METRIC_IDS = {
    "ps_vs_3y_median": "ps_vs_selected_median",
    "pe_vs_3y_median": "pe_vs_selected_median",
    "ev_ebitda_vs_5y_median": "ev_ebitda_vs_selected_median",
    "price_to_cfo_vs_5y_median": "price_to_cfo_vs_selected_median",
}


def migrate_v3_to_v4(config: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(config)
    migrated["version"] = 4
    migrated["scoring_model"] = SCORING_MODEL
    migrated["calibration_version"] = CALIBRATION_VERSION
    reconcile_v3_groups(migrated.get("tab_groups", {}), migrated.get("metrics", {}))
    ensure_v4_defaults(migrated)
    return migrated


def migrate_v4_to_v5(config: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(config)
    migrated["version"] = 5
    migrated["calibration_version"] = CALIBRATION_VERSION
    migrated["missing_policy"] = deepcopy(DEFAULT_MISSING_POLICY)
    migrated["minimum_weight_coverage"] = deepcopy(DEFAULT_MINIMUM_COVERAGE)
    migrated["coverage_policy"] = {
        "minimum_to_score": deepcopy(DEFAULT_MINIMUM_COVERAGE),
        "minimum_for_full_confidence": deepcopy(DEFAULT_FULL_CONFIDENCE_COVERAGE),
    }
    migrated["data_quality"] = default_data_quality_config()
    migrated.setdefault(
        "rating_gates",
        {
            "minimum_data_quality_for_rating": 40,
            "very_strong": {
                "minimum_overall_score": 80,
                "minimum_data_quality": 65,
                "minimum_each_tab": 40,
                "minimum_fundamentals": 50,
            },
            "strong": {
                "minimum_overall_score": 67,
                "minimum_data_quality": 55,
                "minimum_each_tab": 30,
                "minimum_fundamentals": 45,
            },
        },
    )
    ensure_v5_defaults(migrated)
    return migrated


def reconcile_v3_groups(groups_by_tab: dict[str, Any], metrics_by_tab: dict[str, Any]) -> None:
    """Remove v3 cross-profile group references that were never valid for the active metric set."""
    for tab_name, groups in groups_by_tab.items():
        known = {
            LEGACY_METRIC_IDS.get(metric.get("id"), metric.get("id"))
            for metric in metrics_by_tab.get(tab_name, [])
            if isinstance(metric, dict)
        }
        for definition in groups.values():
            members = [LEGACY_METRIC_IDS.get(item, item) for item in definition.get("metrics", [])]
            definition["metrics"] = [item for item in members if item in known]


def ensure_v4_defaults(migrated: dict[str, Any]) -> None:
    """Compatibility alias used by the v3 migration."""
    ensure_v5_defaults(migrated)


def ensure_v5_defaults(migrated: dict[str, Any]) -> None:
    data_quality_defaults = default_data_quality_config()
    data_quality = migrated.setdefault("data_quality", deepcopy(data_quality_defaults))
    if isinstance(data_quality, dict):
        current_weights = data_quality.get("weights")
        if isinstance(current_weights, dict) and set(current_weights) == set(data_quality_defaults["component_weights"]):
            data_quality.setdefault("component_weights", deepcopy(current_weights))
            data_quality["weights"] = deepcopy(data_quality_defaults["weights"])
        else:
            data_quality.setdefault("component_weights", deepcopy(data_quality_defaults["component_weights"]))
            data_quality.setdefault("weights", deepcopy(data_quality_defaults["weights"]))
    migrated.setdefault("profile_overrides", default_profile_overrides())
    migrated.setdefault(
        "rating_gates",
        {
            "minimum_data_quality_for_rating": 40,
            "very_strong": {
                "minimum_overall_score": 80,
                "minimum_data_quality": 65,
                "minimum_each_tab": 40,
                "minimum_fundamentals": 50,
            },
            "strong": {
                "minimum_overall_score": 67,
                "minimum_data_quality": 55,
                "minimum_each_tab": 30,
                "minimum_fundamentals": 45,
            },
        },
    )
    migrated.setdefault("profile_metrics", {})
    migrated.setdefault("model_applicability", {
        "native": 90,
        "generic_financial_maximum": 65,
        "manual_override_without_evidence": 60,
    })
    migrated.setdefault("absolute_guardrails", {
        "fcf_margin": [{"at_or_below": 0, "maximum_score": 35, "reason": "non_positive_fcf"}],
        "equity_to_assets": [{"at_or_below": 0, "maximum_score": 30, "reason": "negative_equity"}],
        "interest_coverage": [{"at_or_below": 1, "maximum_score": 25, "reason": "interest_not_covered"}],
        "roic": [{"at_or_below": 0, "maximum_score": 30, "reason": "non_positive_roic"}],
    })
    financial_metrics = migrated.get("profile_metrics", {}).get("Financial")
    if financial_metrics:
        for profile in specialized_financial_profiles():
            migrated["profile_metrics"].setdefault(profile, deepcopy(financial_metrics))
    migrated.setdefault("profile_models", {})
    for profile in specialized_financial_profiles():
        migrated["profile_models"].setdefault(profile, "generic_financial_fallback")
    migrated.setdefault("profile_tab_groups", {})
    for profile in ["Financial", *specialized_financial_profiles()]:
        migrated["profile_tab_groups"].setdefault(profile, financial_groups(profile))
    migrated.setdefault("profile_rules", {})
    for profile, rules in default_profile_rules().items():
        migrated["profile_rules"].setdefault(profile, rules)


def specialized_financial_profiles() -> list[str]:
    return [
        "FinancialBank",
        "FinancialBroker",
        "FinancialLender",
        "FinancialInsurance",
        "FinancialAssetManager",
        "REIT",
    ]


def financial_groups(profile: str) -> dict[str, Any]:
    capital_weight = 0.45 if profile in {"FinancialBank", "FinancialLender"} else 0.35
    return {
        "Growth": {
            "business_growth": {
                "weight": 0.85,
                "metrics": ["revenue_ttm_range_growth", "net_income_range_growth", "share_count_cagr"],
            },
            "market_context": {
                "weight": 0.15,
                "metrics": ["price_change", "revenue_estimate_growth", "eps_estimate_avg_growth"],
            },
        },
        "Fundamentals": {
            "capital": {"weight": capital_weight, "metrics": ["equity_to_assets"]},
            "quality": {
                "weight": 1 - capital_weight,
                "metrics": ["return_on_assets", "return_on_equity", "net_margin"],
            },
        },
        "Value": {
            "historical_or_peer": {
                "weight": 0.45,
                "metrics": ["pe_vs_selected_median", "pb_vs_selected_median"],
            },
            "absolute_multiples": {
                "weight": 0.45,
                "metrics": ["pe_current", "pb_current"],
            },
            "analyst_context": {"weight": 0.10, "metrics": ["price_target"]},
        },
    }

def default_profile_rules() -> dict[str, Any]:
    industrial = {
        "Growth": {
            "minimum_coverage": 0.55,
            "required_groups": {
                "historical": {"minimum_available_metrics": 1},
                "forward_or_trend": {"minimum_available_metrics": 1},
            },
        },
        "Fundamentals": {
            "minimum_coverage": 0.60,
            "required_groups": {
                "solvency": {"minimum_available_metrics": 1},
                "quality": {"minimum_available_metrics": 2},
            },
        },
        "Value": {
            "minimum_coverage": 0.50,
            "required_groups": {
                "historical_multiples": {"minimum_available_metrics": 2},
                "absolute_multiples": {"minimum_available_metrics": 1},
                "absolute_cash_yield": {"minimum_available_metrics": 1},
            },
        },
    }
    financial = {
        "Growth": {
            "minimum_coverage": 0.55,
            "required_groups": {"business_growth": {"minimum_available_metrics": 1}},
        },
        "Fundamentals": {
            "minimum_coverage": 0.60,
            "required_groups": {
                "capital": {"minimum_available_metrics": 1},
                "quality": {"minimum_available_metrics": 2},
            },
        },
        "Value": {
            "minimum_coverage": 0.50,
            "required_groups": {
                "historical_or_peer": {"minimum_available_metrics": 1},
                "absolute_multiples": {"minimum_available_metrics": 1},
            },
        },
    }
    specialized = {profile: deepcopy(financial) for profile in specialized_financial_profiles()}
    specialized["FinancialBroker"]["Fundamentals"]["minimum_coverage"] = 0.60
    specialized["FinancialLender"]["Value"]["minimum_coverage"] = 0.50
    specialized["FinancialBroker"]["Growth"]["required_groups"]["business_growth"][
        "minimum_available_metrics"
    ] = 2
    return {"Industrial": industrial, "Financial": financial, **specialized}


def default_profile_overrides() -> dict[str, str]:
    return {
        "FUTU": "FinancialBroker",
        "AFRM": "FinancialLender",
        "JPM": "FinancialBank",
        "BAC": "FinancialBank",
        "XTB.WA": "FinancialBroker",
        "PKO.WA": "FinancialBank",
        "PEO.WA": "FinancialBank",
        "PZU.WA": "FinancialInsurance",
    }


def default_data_quality_config() -> dict[str, Any]:
    return {
        "display_name": "Data Quality",
        "display_unit": "points",
        "maximum": 95,
        "weights": {
            "metric_weight_coverage": 0.20,
            "tab_completeness": 0.10,
            "filing_freshness": 0.15,
            "actual_observation_depth": 0.10,
            "source_provenance": 0.15,
            "cross_source_reconciliation": 0.10,
            "temporal_alignment": 0.10,
            "estimate_quality": 0.05,
            "profile_fit": 0.05,
        },
        "component_weights": {
            "effective_metric_coverage": 0.50,
            "data_freshness": 0.25,
            "source_quality": 0.15,
            "cross_source_reconciliation": 0.10,
        },
    }
