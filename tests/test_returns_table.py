from __future__ import annotations

import unittest
from datetime import date

from ticker_analyzer.returns_table import (
    ReturnsTableError,
    analyze_returns_range,
    parse_returns_table,
)

SAMPLE = b"""Year,Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec,Annual
2024,10.00%,-5.00%,2.00%,0%,0%,0%,0%,0%,0%,0%,0%,0%,6.59%
2023,,,,,,,,,,,1.00%,2.00%,3.02%
"""


class ReturnsTableTest(unittest.TestCase):
    def test_parses_monthly_returns_and_bounds(self):
        table = parse_returns_table(SAMPLE)

        self.assertEqual(table.monthly_returns[(2024, 1)], 0.1)
        self.assertEqual(table.monthly_returns[(2024, 2)], -0.05)
        self.assertEqual(table.first_month, date(2023, 11, 1))
        self.assertEqual(table.last_month, date(2024, 12, 1))

    def test_compounds_full_months_and_builds_growth_of_ten_thousand(self):
        table = parse_returns_table(SAMPLE)
        analysis = analyze_returns_range(table, date(2024, 1, 1), date(2024, 2, 29))

        self.assertAlmostEqual(analysis.period_return, 0.045)
        self.assertAlmostEqual(analysis.growth[0].value, 10_000)
        self.assertAlmostEqual(analysis.growth[-1].value, 10_450)
        self.assertEqual(analysis.covered_months, 2)
        self.assertFalse(analysis.partial_months_estimated)

    def test_prorates_partial_month_geometrically(self):
        table = parse_returns_table(SAMPLE)
        analysis = analyze_returns_range(table, date(2024, 1, 1), date(2024, 1, 15))

        self.assertAlmostEqual(analysis.period_return, 1.1 ** (15 / 31) - 1)
        self.assertTrue(analysis.partial_months_estimated)

    def test_rejects_missing_range_and_invalid_inputs(self):
        table = parse_returns_table(SAMPLE)
        with self.assertRaisesRegex(ReturnsTableError, "no value for Oct 2023"):
            analyze_returns_range(table, date(2023, 10, 1), date(2023, 10, 31))
        with self.assertRaisesRegex(ReturnsTableError, "must not be before"):
            analyze_returns_range(table, date(2024, 2, 1), date(2024, 1, 1))
        with self.assertRaisesRegex(ValueError, "positive"):
            analyze_returns_range(table, date(2024, 1, 1), date(2024, 1, 1), initial_capital=0)

    def test_rejects_unsupported_or_malformed_csv(self):
        with self.assertRaisesRegex(ReturnsTableError, "empty"):
            parse_returns_table(b"")
        with self.assertRaisesRegex(ReturnsTableError, "Unsupported"):
            parse_returns_table(b"Year,Jan\n2024,1%\n")
        with self.assertRaisesRegex(ReturnsTableError, "Invalid percentage"):
            parse_returns_table(SAMPLE.replace(b"10.00%", b"nope"))
        with self.assertRaisesRegex(ReturnsTableError, "Invalid percentage"):
            parse_returns_table(SAMPLE.replace(b"10.00%", b"nan%"))
        with self.assertRaisesRegex(ReturnsTableError, "greater than -100"):
            parse_returns_table(SAMPLE.replace(b"10.00%", b"-100%"))


if __name__ == "__main__":
    unittest.main()
