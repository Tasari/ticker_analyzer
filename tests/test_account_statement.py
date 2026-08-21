from __future__ import annotations

import unittest
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from ticker_analyzer.account_statement import (
    AccountStatementError,
    inspect_account_statement,
    read_statement_sheet,
)


def statement_workbook(*, include_summary: bool = True) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Account Summary" if include_summary else "Other"
    summary.append(["Details", "Totals"])
    summary.append(["Currency", "USD"])
    summary.append(["Start Date", "01/06/2023 00:00:00"])
    summary.append(["End Date", "31/08/2023 23:59:59"])
    holdings = workbook.create_sheet("Holdings")
    holdings.append(["Asset", "Position ID", "Value in USD"])
    holdings.append(["Apple", 123, 10.5])
    holdings.append(["Bitcoin", 456, 20.25])
    glossary = workbook.create_sheet("Glossary")
    glossary.append(["Account Statement Glossary"])
    glossary.append(["Term", "Meaning"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


class AccountStatementTest(unittest.TestCase):
    def test_inspects_etoro_workbook_metadata_and_sheet_sizes(self):
        overview = inspect_account_statement(statement_workbook())

        self.assertEqual(overview.currency, "USD")
        self.assertEqual(overview.start_date, datetime(2023, 6, 1))
        self.assertEqual(overview.end_date, datetime(2023, 8, 31, 23, 59, 59))
        self.assertEqual(
            [(sheet.name, sheet.data_rows, sheet.columns) for sheet in overview.sheets],
            [
                ("Account Summary", 3, 2),
                ("Holdings", 2, 3),
                ("Glossary", 1, 2),
            ],
        )

    def test_reads_bounded_sheet_preview(self):
        preview = read_statement_sheet(statement_workbook(), "Holdings", max_rows=1)

        self.assertEqual(preview.columns, ("Asset", "Position ID", "Value in USD"))
        self.assertEqual(preview.rows, (("Apple", 123, 10.5),))
        self.assertEqual(preview.total_rows, 2)
        self.assertTrue(preview.truncated)

    def test_preview_keeps_columns_missing_from_the_first_row(self):
        preview = read_statement_sheet(statement_workbook(), "Glossary")

        self.assertEqual(preview.columns, ("Account Statement Glossary", "Column 2"))
        self.assertEqual(preview.rows, (("Term", "Meaning"),))

    def test_rejects_non_xlsx_and_workbook_without_account_summary(self):
        with self.assertRaisesRegex(AccountStatementError, "not an XLSX"):
            inspect_account_statement(b"not a workbook")
        with self.assertRaisesRegex(AccountStatementError, "missing sheet Account Summary"):
            inspect_account_statement(statement_workbook(include_summary=False))

    def test_rejects_unknown_sheet_and_invalid_preview_limit(self):
        payload = statement_workbook()
        with self.assertRaisesRegex(AccountStatementError, "not present"):
            read_statement_sheet(payload, "Closed Positions")
        with self.assertRaisesRegex(ValueError, "positive"):
            read_statement_sheet(payload, "Holdings", max_rows=0)


if __name__ == "__main__":
    unittest.main()
