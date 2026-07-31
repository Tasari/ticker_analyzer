from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

FallbackLevel = Literal[
    "none",
    "same_source",
    "annual_fallback",
    "info_fallback",
    "secondary_source",
    "estimated",
]


@dataclass(frozen=True)
class DataProvenance:
    provider: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_url: str | None = None
    period_end: datetime | None = None
    filed_at: datetime | None = None
    form: str | None = None
    accession_number: str | None = None
    observation_count: int = 1
    fallback_level: FallbackLevel = "none"
    is_primary_source: bool = False

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("fetched_at", "period_end", "filed_at"):
            value = result.get(key)
            result[key] = value.isoformat() if value is not None else None
        return result


@dataclass
class RawMetric:
    value: float | None
    note: str = ""
    provenance: DataProvenance | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "note": self.note,
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }


@dataclass(frozen=True)
class AnalysisRanges:
    growth: str
    fundamentals: str
    value: str

    @classmethod
    def from_input(cls, ranges: str | dict[str, str]) -> AnalysisRanges:
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
    def from_config(cls, config: dict[str, Any]) -> MetricDefinition:
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
    provenance: dict[str, Any] | None = None


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
    coverage: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    data_quality: float = 0.0
    data_quality_breakdown: dict[str, Any] = field(default_factory=dict)
    scoring_version: int = 4
    config_version: int = 4
    calibration_version: str = "v4-bootstrap-2026Q3"
    diagnostics: list[dict[str, str]] = field(default_factory=list)

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
            "coverage": self.coverage,
            "confidence": self.confidence,
            "confidence_breakdown": self.confidence_breakdown,
            "data_quality": self.data_quality,
            "data_quality_breakdown": self.data_quality_breakdown,
            "scoring_version": self.scoring_version,
            "config_version": self.config_version,
            "calibration_version": self.calibration_version,
            "diagnostics": self.diagnostics,
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
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    provenance: dict[str, DataProvenance] = field(default_factory=dict)
    official_ids: dict[str, Any] = field(default_factory=dict)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
