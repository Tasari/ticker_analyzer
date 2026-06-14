from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class AnalysisRanges:
    growth: str
    fundamentals: str
    value: str

    @classmethod
    def from_input(cls, ranges: str | dict[str, str]) -> "AnalysisRanges":
        if isinstance(ranges, str):
            return cls(growth=ranges, fundamentals=ranges, value=ranges)
        return cls(
            growth=ranges.get("Growth", "2Y"),
            fundamentals=ranges.get("Fundamentals", "2Y"),
            value=ranges.get("Value", "2Y"),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "Growth": self.growth,
            "Fundamentals": self.fundamentals,
            "Value": self.value,
        }


@dataclass
class MetricDefinition:
    id: str
    name: str
    weight: float
    direction: str
    warn: float | None
    good: float | None
    unit: str
    description: str = ""
    benchmark: float | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MetricDefinition":
        return cls(
            id=config["id"],
            name=config.get("name", config["id"]),
            weight=float(config.get("weight", 0) or 0),
            direction=config.get("direction", "higher"),
            warn=_optional_float(config.get("warn")),
            good=_optional_float(config.get("good")),
            unit=config.get("unit", ""),
            description=config.get("description", ""),
            benchmark=_optional_float(config.get("benchmark")),
        )


@dataclass
class MetricResult:
    id: str
    name: str
    value: float | None
    unit: str
    score: float | None
    weight: float
    status: str
    note: str = ""
    description: str = ""


@dataclass
class CategoryAnalysis:
    name: str
    score: float | None
    rating: str
    metrics: list[MetricResult]


@dataclass
class StockAnalysis:
    ticker: str
    company_name: str
    currency: str
    profile: str
    current_price: float | None
    overall_score: float | None
    rating: str
    tabs: dict[str, dict[str, Any]]
    missing: list[str]
    raw: dict[str, dict[str, Any]]
    ranges: dict[str, str]
    charts: dict[str, pd.DataFrame]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "currency": self.currency,
            "profile": self.profile,
            "current_price": self.current_price,
            "overall_score": self.overall_score,
            "rating": self.rating,
            "tabs": self.tabs,
            "missing": self.missing,
            "raw": self.raw,
            "ranges": self.ranges,
            "charts": self.charts,
        }


@dataclass
class MarketData:
    ticker: str
    info: dict[str, Any]
    annual_income: pd.DataFrame
    annual_balance: pd.DataFrame
    annual_cashflow: pd.DataFrame
    quarterly_income: pd.DataFrame
    quarterly_balance: pd.DataFrame
    quarterly_cashflow: pd.DataFrame
    growth_history: pd.DataFrame
    value_history: pd.DataFrame
    analyst_targets: dict[str, Any]
    revenue_estimate: pd.DataFrame
    earnings_estimate: pd.DataFrame
    eps_trend: pd.DataFrame
    growth_estimates: pd.DataFrame


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
