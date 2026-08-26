from __future__ import annotations

from typing import Any

from ticker_analyzer.metrics.formulas import is_financial_company

_IDENTIFIER_PROFILES = {
    "fdic_cert": "FinancialBank",
    "finra_crd": "FinancialBroker",
    "naic_code": "FinancialInsurance",
}
_INDUSTRY_PROFILE_RULES = (
    ("REIT", frozenset(), ("6798",), ("reit",)),
    ("FinancialInsurance", frozenset(), ("63",), ("insurance", "reinsurance")),
    ("FinancialAssetManager", frozenset({"6282"}), (), ("asset management", "investment management")),
    ("FinancialBroker", frozenset({"6211"}), (), ("capital markets", "broker", "securities")),
    ("FinancialLender", frozenset({"6141", "6153", "6159", "6162", "6163"}), (), ("credit services", "consumer finance", "mortgage finance")),
    ("FinancialBank", frozenset({"6021", "6022", "6029", "6035", "6036"}), (), ("bank",)),
)


def company_profile(
    info: dict[str, Any],
    ticker: str = "",
    official_ids: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> str:
    overrides = (config or {}).get("profile_overrides", {})
    normalized_ticker = ticker.strip().upper()
    if normalized_ticker in overrides:
        return str(overrides[normalized_ticker])
    identifiers = official_ids or {}
    for identifier, profile in _IDENTIFIER_PROFILES.items():
        if identifiers.get(identifier):
            return profile
    industry = str(info.get("industry") or info.get("industryDisp") or "").lower()
    sector = str(info.get("sector") or "").lower()
    sic = str(identifiers.get("sec_sic") or identifiers.get("sic") or info.get("sic") or "")
    for profile, exact_sics, sic_prefixes, industry_terms in _INDUSTRY_PROFILE_RULES:
        if (
            sic in exact_sics
            or any(sic.startswith(prefix) for prefix in sic_prefixes)
            or any(term in industry for term in industry_terms)
        ):
            return profile
    if is_financial_company(info) or sector == "financial services":
        return "Financial"
    return "Industrial"


def config_for_profile(config: dict[str, Any], profile: str) -> dict[str, Any]:
    profile_metrics = config.get("profile_metrics", {}).get(profile)
    if not profile_metrics and profile.startswith("Financial"):
        profile_metrics = config.get("profile_metrics", {}).get("Financial")
    selected = dict(config)
    selected["active_profile"] = profile
    selected["active_metric_model"] = config.get("profile_models", {}).get(profile, "native")
    if selected["active_metric_model"] == "generic_financial_fallback":
        selected["active_rating_cap"] = "strong"
    if profile_metrics:
        selected["metrics"] = profile_metrics
    selected["tab_groups"] = config.get("profile_tab_groups", {}).get(
        profile,
        config.get("profile_tab_groups", {}).get("Financial", config.get("tab_groups", {}))
        if profile.startswith("Financial") or profile == "REIT"
        else config.get("tab_groups", {}),
    )
    selected["active_profile_rules"] = config.get("profile_rules", {}).get(
        profile,
        config.get("profile_rules", {}).get("Financial", {})
        if profile.startswith("Financial") or profile == "REIT"
        else config.get("profile_rules", {}).get("Industrial", {}),
    )
    return selected
