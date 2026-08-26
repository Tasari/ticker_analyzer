from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import pandas as pd

RatingCode = Literal[
    "very_strong", "strong", "neutral", "weak", "very_weak", "not_rated", "insufficient_data"
]

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
    data_as_of: datetime | None = None

    @classmethod
    def from_input(cls, ranges: str | dict[str, str]) -> AnalysisRanges:
        if isinstance(ranges, str):
            return cls(growth=ranges, fundamentals=ranges, value=ranges)
        as_of = pd.to_datetime(ranges.get("data_as_of") or ranges.get("_data_as_of"), utc=True, errors="coerce")
        return cls(
            growth=ranges.get("Growth", "2Y"),
            fundamentals=ranges.get("Fundamentals", "2Y"),
            value=ranges.get("Value", "2Y"),
            data_as_of=None if pd.isna(as_of) else pd.Timestamp(as_of).to_pydatetime(),
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
    rating_code: RatingCode = "insufficient_data"
    tabs: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    raw: dict[str, dict[str, Any]] = field(default_factory=dict)
    ranges: dict[str, str] = field(default_factory=dict)
    charts: dict[str, pd.DataFrame] = field(default_factory=dict)
    coverage: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    confidence_breakdown: dict[str, Any] = field(default_factory=dict)
    data_quality: float = 0.0
    data_quality_breakdown: dict[str, Any] = field(default_factory=dict)
    model_applicability: float = 100.0
    rating_confidence: str = "None"
    rating_status: str = "insufficient_data"
    rating_caps: list[str] = field(default_factory=list)
    rating_reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scoring_version: int = 5
    config_version: int = 5
    calibration_version: str = "v5.2-value-2026Q3"
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
            "rating_code": self.rating_code,
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
            "model_applicability": self.model_applicability,
            "rating_confidence": self.rating_confidence,
            "rating_status": self.rating_status,
            "rating_caps": self.rating_caps,
            "rating_reason_codes": self.rating_reason_codes,
            "warnings": self.warnings,
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
