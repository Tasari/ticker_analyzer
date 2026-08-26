from __future__ import annotations

import csv
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from math import isfinite

MAX_RETURNS_UPLOAD_BYTES = 1024 * 1024
ACCOUNT_STATEMENT_TICKER = "ACC_STMT"
ACCOUNT_RETURNS_STATE_KEY = "account_statement_returns_table"
ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY = "account_statement_payload"
ACCOUNT_STATEMENT_NAME_STATE_KEY = "account_statement_filename"
ACCOUNT_RETURNS_PAYLOAD_STATE_KEY = "account_returns_payload"
ACCOUNT_RETURNS_NAME_STATE_KEY = "account_returns_filename"
MONTH_COLUMNS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class ReturnsTableError(ValueError):
    """Raised when an uploaded returns table cannot be used."""


@dataclass(frozen=True)
class ReturnsTable:
    monthly_returns: dict[tuple[int, int], float]

    @property
    def first_month(self) -> date:
        year, month = min(self.monthly_returns)
        return date(year, month, 1)

    @property
    def last_month(self) -> date:
        year, month = max(self.monthly_returns)
        return date(year, month, 1)


@dataclass(frozen=True)
class GrowthPoint:
    day: date
    value: float


@dataclass(frozen=True)
class ReturnsRangeAnalysis:
    period_return: float
    annualized_return: float | None
    growth: tuple[GrowthPoint, ...]
    covered_months: int
    partial_months_estimated: bool


def parse_returns_table(payload: bytes) -> ReturnsTable:
    if not payload:
        raise ReturnsTableError("The returns table is empty.")
    if len(payload) > MAX_RETURNS_UPLOAD_BYTES:
        raise ReturnsTableError("The returns table is larger than the 1 MB upload limit.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReturnsTableError("The returns table must be a UTF-8 CSV file.") from exc
    reader = csv.DictReader(StringIO(text))
    headers = tuple((header or "").strip() for header in (reader.fieldnames or ()))
    required = {"Year", *MONTH_COLUMNS}
    if not required.issubset(headers):
        raise ReturnsTableError(
            "Unsupported returns table columns. Expected Year and Jan through Dec."
        )
    reader.fieldnames = list(headers)

    monthly_returns: dict[tuple[int, int], float] = {}
    for row_number, row in enumerate(reader, start=2):
        raw_year = (row.get("Year") or "").strip()
        if not raw_year:
            continue
        try:
            year = int(raw_year)
        except ValueError as exc:
            raise ReturnsTableError(f"Invalid year in row {row_number}.") from exc
        if not 1900 <= year <= 2200:
            raise ReturnsTableError(f"Year in row {row_number} is outside the supported range.")
        for month, column in enumerate(MONTH_COLUMNS, start=1):
            raw_return = (row.get(column) or "").strip()
            if not raw_return:
                continue
            key = (year, month)
            if key in monthly_returns:
                raise ReturnsTableError(f"Duplicate return for {column} {year}.")
            monthly_returns[key] = _parse_percentage(raw_return, row_number, column)
    if not monthly_returns:
        raise ReturnsTableError("The returns table contains no monthly returns.")
    return ReturnsTable(monthly_returns=monthly_returns)


def analyze_returns_range(
    table: ReturnsTable,
    start_date: date,
    end_date: date,
    *,
    initial_capital: float = 10_000.0,
) -> ReturnsRangeAnalysis:
    if end_date < start_date:
        raise ReturnsTableError("The selected end date must not be before the start date.")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")

    growth = [GrowthPoint(day=start_date, value=initial_capital)]
    capital = initial_capital
    current_month = date(start_date.year, start_date.month, 1)
    covered_months = 0
    partial_months_estimated = False
    while current_month <= end_date:
        key = (current_month.year, current_month.month)
        if key not in table.monthly_returns:
            raise ReturnsTableError(
                f"The returns table has no value for {MONTH_COLUMNS[current_month.month - 1]} "
                f"{current_month.year}."
            )
        month_end = date(
            current_month.year,
            current_month.month,
            monthrange(current_month.year, current_month.month)[1],
        )
        overlap_start = max(start_date, current_month)
        overlap_end = min(end_date, month_end)
        overlap_days = (overlap_end - overlap_start).days + 1
        days_in_month = month_end.day
        monthly_return = table.monthly_returns[key]
        fraction = overlap_days / days_in_month
        if overlap_days != days_in_month:
            partial_months_estimated = True
        capital *= (1 + monthly_return) ** fraction
        growth.append(GrowthPoint(day=overlap_end, value=capital))
        covered_months += 1
        current_month = month_end + timedelta(days=1)

    period_return = capital / initial_capital - 1
    duration_years = ((end_date - start_date).days + 1) / 365.2425
    annualized_return = (
        (1 + period_return) ** (1 / duration_years) - 1
        if period_return > -1 and duration_years > 0
        else None
    )
    return ReturnsRangeAnalysis(
        period_return=period_return,
        annualized_return=annualized_return,
        growth=tuple(growth),
        covered_months=covered_months,
        partial_months_estimated=partial_months_estimated,
    )


def _parse_percentage(raw_value: str, row_number: int, column: str) -> float:
    normalized = raw_value.strip().replace(" ", "")
    normalized = normalized.removesuffix("%")
    try:
        value = float(normalized) / 100
    except ValueError as exc:
        raise ReturnsTableError(
            f"Invalid percentage in {column}, row {row_number}."
        ) from exc
    if not isfinite(value):
        raise ReturnsTableError(
            f"Invalid percentage in {column}, row {row_number}."
        )
    if value <= -1:
        raise ReturnsTableError(
            f"Return in {column}, row {row_number} must be greater than -100%."
        )
    return value
