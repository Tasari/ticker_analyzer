from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.domain import DataProvenance, MarketData
from ticker_analyzer.metrics.utils import clean_number


def attach_metric_provenance(raw_metrics: dict[str, dict[str, Any]], data: MarketData) -> None:
    estimate_metrics = {
        "revenue_estimate_growth",
        "eps_estimate_avg_growth",
        "price_target",
        "upside_vs_configured_benchmark",
    }
    price_metrics = {
        "price_change",
        "ps_vs_selected_median",
        "pe_vs_selected_median",
        "pb_vs_selected_median",
        "ev_ebitda_vs_selected_median",
        "price_to_cfo_vs_selected_median",
        "fcf_yield",
        "fcf_yield_ttm",
        "pe_vs_profile_median",
        "ev_ebitda_vs_profile_median",
        "fcf_yield_vs_profile_median",
        "valuation_growth_adjustment",
    }
    for metric_id, raw in raw_metrics.items():
        source = "estimates" if metric_id in estimate_metrics else "prices" if metric_id in price_metrics else "financials"
        provenance = data.provenance.get(source)
        if provenance is None:
            provenance = DataProvenance(
                provider="unavailable",
                observation_count=0,
                fallback_level="estimated",
                is_primary_source=False,
            )
        input_facts: list[dict[str, Any]] = []
        if source == "financials":
            for statement_name in (
                "annual_income", "annual_balance", "annual_cashflow",
                "quarterly_income", "quarterly_balance", "quarterly_cashflow",
            ):
                frame = getattr(data, statement_name)
                for (fact_name, period), versions in frame.attrs.get("observation_provenance", {}).items():
                    input_facts.append(
                        {
                            "fact": fact_name,
                            "period_end": pd.Timestamp(period).date().isoformat(),
                            "statement": statement_name,
                            "versions": versions,
                        }
                    )
                if not frame.attrs.get("observation_provenance"):
                    for fact_name, values in frame.iterrows():
                        for period, value in values.dropna().items():
                            input_facts.append(
                                {
                                    "fact": str(fact_name),
                                    "period_end": pd.Timestamp(period).date().isoformat(),
                                    "statement": statement_name,
                                    "versions": [{"provider": provenance.provider, "value": clean_number(value)}],
                                }
                            )
        elif source == "prices":
            input_facts.append(
                {
                    "fact": "raw_close_history",
                    "period_end": provenance.period_end.isoformat() if provenance.period_end else None,
                    "provider": provenance.provider,
                    "observation_count": provenance.observation_count,
                }
            )
        else:
            input_facts.append(
                {
                    "fact": "analyst_consensus",
                    "provider": provenance.provider,
                    "observation_count": provenance.observation_count,
                    "fallback_level": provenance.fallback_level,
                }
            )
        raw["provenance"] = {**provenance.as_dict(), "input_facts": input_facts}


def apply_peer_calibration(
    raw_metrics: dict[str, dict[str, Any]],
    info: dict[str, Any],
    profile: str,
    config: dict[str, Any],
) -> None:
    medians = config.get("peer_medians", {}).get(profile) or config.get("peer_medians", {}).get("Financial" if profile.startswith("Financial") else profile, {})
    comparisons = {
        "pe_vs_profile_median": (clean_number(info.get("trailingPE")), clean_number(medians.get("pe")), False),
        "ev_ebitda_vs_profile_median": (
            clean_number(info.get("enterpriseToEbitda")),
            clean_number(medians.get("ev_ebitda")),
            False,
        ),
        "fcf_yield_vs_profile_median": (
            clean_number(raw_metrics.get("fcf_yield_ttm", {}).get("value")),
            clean_number(medians.get("fcf_yield")),
            True,
        ),
    }
    for metric_id, (current, peer, higher_is_better) in comparisons.items():
        if current is None or peer in (None, 0):
            continue
        relative = (current / peer - 1) * 100
        raw_metrics[metric_id] = {
            "value": relative,
            "note": f"Compared with versioned {profile} peer median; "
            + ("positive is better" if higher_is_better else "negative is cheaper"),
        }
