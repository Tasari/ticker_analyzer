from __future__ import annotations

import unittest
from datetime import date

import pandas as pd
from ticker_analyzer.portfolio_performance import (
    BenchmarkError,
    benchmark_growth_from_history,
    calculate_drawdown,
    monthly_performance,
    parse_comparison_symbols,
)
from ticker_analyzer.returns_table import GrowthPoint


class PortfolioPerformanceTest(unittest.TestCase):
    def test_benchmark_normalizes_prices_and_calculates_drawdown(self):
        history = pd.DataFrame(
            {"Close": [100, 120, 90, 110]},
            index=pd.to_datetime(["2024-01-02", "2024-01-31", "2024-02-15", "2024-02-29"]),
        )
        growth = benchmark_growth_from_history(history, date(2024, 1, 1), date(2024, 2, 29))

        self.assertEqual(growth[0], GrowthPoint(date(2024, 1, 1), 10_000))
        self.assertAlmostEqual(growth[-1].value, 11_000)
        drawdown = calculate_drawdown(growth)
        self.assertIsNotNone(drawdown)
        self.assertAlmostEqual(drawdown.value, -0.25)
        self.assertEqual(drawdown.peak_date, date(2024, 1, 31))
        self.assertEqual(drawdown.trough_date, date(2024, 2, 15))

    def test_monthly_performance_uses_last_value_in_each_month(self):
        points = (
            GrowthPoint(date(2024, 1, 1), 10_000),
            GrowthPoint(date(2024, 1, 31), 11_000),
            GrowthPoint(date(2024, 2, 29), 9_900),
        )

        result = monthly_performance(points)

        self.assertEqual([item.month for item in result], [date(2024, 1, 1), date(2024, 2, 1)])
        self.assertAlmostEqual(result[0].return_value, 0.1)
        self.assertAlmostEqual(result[1].return_value, -0.1)

    def test_empty_benchmark_is_rejected(self):
        with self.assertRaises(BenchmarkError):
            benchmark_growth_from_history(pd.DataFrame(), date(2024, 1, 1), date(2024, 2, 1))

    def test_comparison_symbols_are_normalized_and_deduplicated(self):
        self.assertEqual(
            parse_comparison_symbols(" spy, QQQ;spy  vwce.de "),
            ("SPY", "QQQ", "VWCE.DE"),
        )

    def test_comparison_symbols_have_a_bounded_limit(self):
        with self.assertRaisesRegex(BenchmarkError, "no more than 2"):
            parse_comparison_symbols("SPY QQQ DIA", maximum=2)


if __name__ == "__main__":
    unittest.main()
