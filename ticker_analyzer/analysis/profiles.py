from __future__ import annotations

from typing import Any

from ticker_analyzer.metrics.formulas import is_financial_company


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
    if identifiers.get("fdic_cert"):
        return "FinancialBank"
    if identifiers.get("finra_crd"):
        return "FinancialBroker"
    if identifiers.get("naic_code"):
        return "FinancialInsurance"
    industry = str(info.get("industry") or info.get("industryDisp") or "").lower()
    sector = str(info.get("sector") or "").lower()
    sic = str(identifiers.get("sec_sic") or identifiers.get("sic") or info.get("sic") or "")
    if "reit" in industry or sic.startswith("6798"):
        return "REIT"
    if sic.startswith("63") or any(term in industry for term in ("insurance", "reinsurance")):
        return "FinancialInsurance"
    if sic == "6282" or any(term in industry for term in ("asset management", "investment management")):
        return "FinancialAssetManager"
    if sic == "6211" or any(term in industry for term in ("capital markets", "broker", "securities")):
        return "FinancialBroker"
    if sic in {"6141", "6153", "6159", "6162", "6163"} or any(
        term in industry for term in ("credit services", "consumer finance", "mortgage finance")
    ):
        return "FinancialLender"
    if "bank" in industry or sic in {"6021", "6022", "6029", "6035", "6036"}:
        return "FinancialBank"
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

