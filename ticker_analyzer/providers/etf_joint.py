from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from ticker_analyzer.providers.etf import EtfHoldings, normalize_holding_ticker

JOINT_COLUMNS = ["Ticker", "Company", "Joint weight (%)", "ETF count", "ETFs"]


@dataclass
class JointHoldings:
    holdings: pd.DataFrame
    fund_coverage: dict[str, float]
    missing_funds: list[str]


def combine_etf_holdings(
    results: Mapping[str, EtfHoldings], allocations: Mapping[str, float],
) -> JointHoldings:
    """Sum company exposures using portfolio allocations, preserving unpublished exposure."""
    if not allocations or any(not math.isfinite(weight) or weight < 0 for weight in allocations.values()):
        raise ValueError("ETF allocations must be finite, non-negative percentages.")
    if not math.isclose(sum(allocations.values()), 100, abs_tol=.01):
        raise ValueError("ETF allocations must total 100%.")
    positions = {}
    coverage = {}
    missing = []
    for fund, allocation in allocations.items():
        if allocation == 0:
            continue
        result = results.get(fund)
        if not isinstance(result, EtfHoldings) or result.ticker != fund:
            missing.append(fund)
            continue
        seen = set()
        coverage[fund] = 0.0
        for _, row in result.holdings.iterrows():
            ticker = normalize_holding_ticker(row["Ticker"], result.source)
            weight = float(row["Weight (%)"])
            if not ticker or ticker in seen or not math.isfinite(weight) or not 0 < weight <= 100:
                continue
            seen.add(ticker)
            contribution = weight * allocation / 100
            coverage[fund] += contribution
            position = positions.setdefault(ticker, {"Ticker": ticker, "Company": row["Company"], "Joint weight (%)": 0.0, "funds": []})
            position["Joint weight (%)"] += contribution
            position["funds"].append(fund)
    rows = [
        {**{key: value for key, value in position.items() if key != "funds"},
         "ETF count": len(position["funds"]), "ETFs": ", ".join(position["funds"])}
        for position in positions.values()
    ]
    frame = pd.DataFrame(rows, columns=JOINT_COLUMNS).sort_values("Joint weight (%)", ascending=False).reset_index(drop=True)
    return JointHoldings(frame, coverage, missing)
