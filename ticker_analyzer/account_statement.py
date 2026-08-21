from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from itertools import islice
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 75 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 250
DEFAULT_PREVIEW_ROWS = 500
REQUIRED_SHEETS = {"Account Summary"}


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


def inspect_account_statement(payload: bytes) -> StatementOverview:
    validate_xlsx_payload(payload)
    try:
        workbook = load_workbook(
            BytesIO(payload),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise AccountStatementError("The uploaded file is not a readable XLSX workbook.") from exc

    try:
        missing = REQUIRED_SHEETS.difference(workbook.sheetnames)
        if missing:
            raise AccountStatementError(
                "This does not look like an eToro account statement: "
                f"missing sheet {', '.join(sorted(missing))}."
            )
        summary = _key_value_rows(workbook["Account Summary"])
        sheets = tuple(
            SheetInfo(name=worksheet.title, data_rows=shape[0], columns=shape[1])
            for worksheet in workbook.worksheets
            for shape in (_worksheet_shape(worksheet),)
        )
        return StatementOverview(
            currency=_optional_text(summary.get("Currency")),
            start_date=_parse_statement_datetime(summary.get("Start Date")),
            end_date=_parse_statement_datetime(summary.get("End Date")),
            sheets=sheets,
        )
    finally:
        workbook.close()


def read_statement_sheet(
    payload: bytes,
    sheet_name: str,
    *,
    max_rows: int = DEFAULT_PREVIEW_ROWS,
) -> SheetPreview:
    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    validate_xlsx_payload(payload)
    try:
        workbook = load_workbook(
            BytesIO(payload),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise AccountStatementError("The uploaded file is not a readable XLSX workbook.") from exc

    try:
        if sheet_name not in workbook.sheetnames:
            raise AccountStatementError(f"Sheet {sheet_name!r} is not present in the workbook.")
        worksheet = workbook[sheet_name]
        total_rows, column_count = _worksheet_shape(worksheet)
        iterator = worksheet.iter_rows(values_only=True)
        first_row = next(iterator, ())
        padded_header = tuple(first_row) + (None,) * max(column_count - len(first_row), 0)
        columns = _unique_headers(padded_header)
        rows = tuple(
            tuple(row[index] if index < len(row) else None for index in range(len(columns)))
            for row in islice(iterator, max_rows)
        )
        return SheetPreview(
            columns=columns,
            rows=rows,
            total_rows=total_rows,
        )
    finally:
        workbook.close()


def validate_xlsx_payload(payload: bytes) -> None:
    if not payload:
        raise AccountStatementError("The uploaded file is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise AccountStatementError("The workbook is larger than the 10 MB upload limit.")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise AccountStatementError("The workbook contains too many internal files.")
            if sum(member.file_size for member in members) > MAX_UNCOMPRESSED_BYTES:
                raise AccountStatementError("The expanded workbook is too large to process safely.")
            if "xl/workbook.xml" not in archive.namelist():
                raise AccountStatementError("The uploaded file is not an XLSX workbook.")
    except BadZipFile as exc:
        raise AccountStatementError("The uploaded file is not an XLSX workbook.") from exc


def _key_value_rows(worksheet: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in worksheet.iter_rows(min_col=1, max_col=2, values_only=True):
        key = _optional_text(row[0])
        if key:
            values[key] = row[1]
    return values


def _worksheet_shape(worksheet: Any) -> tuple[int, int]:
    if worksheet.max_row and worksheet.max_column:
        return max(worksheet.max_row - 1, 0), worksheet.max_column
    row_count = 0
    column_count = 0
    for row in worksheet.iter_rows(values_only=True):
        row_count += 1
        column_count = max(column_count, len(row))
    return max(row_count - 1, 0), column_count


def _unique_headers(values: tuple[Any, ...]) -> tuple[str, ...]:
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(values, start=1):
        base = _optional_text(value) or f"Column {index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return tuple(headers)


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _parse_statement_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = _optional_text(value)
    if not text:
        return None
    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None
