"""Typed results shared by workbook parsing and portfolio analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


class AccountStatementError(ValueError):
    """Raised when an uploaded workbook is not a supported account statement."""


@dataclass(frozen=True)
class SheetInfo:
    name: str
    data_rows: int
    columns: int


@dataclass(frozen=True)
class StatementOverview:
    currency: str | None
    start_date: datetime | None
    end_date: datetime | None
    sheets: tuple[SheetInfo, ...]


@dataclass(frozen=True)
class SheetPreview:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    total_rows: int

    @property
    def truncated(self) -> bool:
        return self.total_rows > len(self.rows)


@dataclass(frozen=True)
class ExternalCashFlow:
    occurred_at: datetime
    amount: float
    kind: str
    estimated_date: bool = False


@dataclass(frozen=True)
class ExposureGroup:
    name: str
    value: float


@dataclass(frozen=True)
class PositionContribution:
    asset: str
    realized_profit_loss: float
    fees_and_dividends: float
    total_contribution: float
    closed_positions: int


@dataclass(frozen=True)
class StatementAnalysis:
    currency: str
    start_date: datetime
    end_date: datetime
    beginning_realized_equity: float
    ending_realized_equity: float
    beginning_unrealized_equity: float
    ending_unrealized_equity: float
    net_external_flows: float
    positive_contributions: float
    total_profit_loss: float
    closed_positions_profit_loss: float
    dividends: float
    fees: float
    other_performance: float
    unrealized_profit_loss_change: float
    simple_roi: float | None
    annualized_roi: float | None
    modified_dietz_return: float | None
    cash_flows: tuple[ExternalCashFlow, ...]
    open_positions: int
    long_exposure: float
    short_exposure: float
    exposure_by_type: tuple[ExposureGroup, ...]
    warnings: tuple[str, ...]
    holdings_snapshot_date: date | None = None


@dataclass(frozen=True)
class DailyPerformancePoint:
    day: date
    cumulative_profit_loss: float
    estimated_cumulative_profit_loss: float | None = None


@dataclass(frozen=True)
class StatementRangeAnalysis:
    start_date: date
    end_date: date
    realized_profit_loss: float
    closed_positions_profit_loss: float
    dividends: float
    fees: float
    other_performance: float
    net_external_flows: float
    positive_contributions: float
    estimated_beginning_equity: float
    estimated_ending_equity: float
    estimated_total_profit_loss: float
    estimated_roi: float | None
    estimated_annualized_roi: float | None
    estimated_modified_dietz_return: float | None
    holdings_snapshot_count: int
    max_boundary_anchor_distance_days: int
    valuation_warnings: tuple[str, ...]
    daily_performance: tuple[DailyPerformancePoint, ...]


@dataclass(frozen=True)
class _UnrealizedEquityAnchor:
    day: date
    unrealized_profit_loss: float
    exact: bool
