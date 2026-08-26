from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from ticker_analyzer.portfolio.performance import (
    BenchmarkError,
    benchmark_growth_from_history,
    calculate_drawdown,
    fetch_benchmark_growth,
    monthly_performance,
    parse_comparison_symbols,
)
from ticker_analyzer.portfolio.returns import GrowthPoint


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

    def test_benchmark_rejects_missing_close_and_unusable_prices(self):
        index = pd.to_datetime(["2024-01-02"])
        for history in (
            pd.DataFrame({"Open": [100]}, index=index),
            pd.DataFrame({"Close": [None]}, index=index),
            pd.DataFrame({"Close": [0]}, index=index),
            pd.DataFrame({"Close": [float("inf")]}, index=index),
        ):
            with self.subTest(columns=tuple(history.columns)):
                with self.assertRaises(BenchmarkError):
                    benchmark_growth_from_history(history, date(2024, 1, 1), date(2024, 2, 1))

    def test_fetch_benchmark_validates_symbol_and_wraps_provider_failure(self):
        with self.assertRaisesRegex(BenchmarkError, "ticker"):
            fetch_benchmark_growth(" ", date(2024, 1, 1), date(2024, 2, 1))
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.side_effect = RuntimeError("offline")
            with patch("ticker_analyzer.providers.market_data.retry_transient", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(BenchmarkError, "offline"):
                    fetch_benchmark_growth("spy", date(2024, 1, 1), date(2024, 2, 1))

    def test_fetch_benchmark_uses_adjusted_inclusive_history(self):
        history = pd.DataFrame(
            {"Close": [100, 110]},
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        with patch("yfinance.Ticker") as ticker:
            ticker.return_value.history.return_value = history
            growth = fetch_benchmark_growth(" spy ", date(2024, 1, 1), date(2024, 1, 3))

        ticker.assert_called_once_with("SPY")
        ticker.return_value.history.assert_called_once_with(
            start="2024-01-01", end="2024-01-04", auto_adjust=True, actions=False
        )
        self.assertAlmostEqual(growth[-1].value, 11_000)

    def test_comparison_symbols_are_normalized_and_deduplicated(self):
        self.assertEqual(
            parse_comparison_symbols(" spy, QQQ;spy  vwce.de "),
            ("SPY", "QQQ", "VWCE.DE"),
        )

    def test_comparison_symbols_have_a_bounded_limit(self):
        with self.assertRaisesRegex(BenchmarkError, "no more than 2"):
            parse_comparison_symbols("SPY QQQ DIA", maximum=2)

    def test_drawdown_and_monthly_performance_handle_boundary_values(self):
        self.assertIsNone(calculate_drawdown(()))
        flat = calculate_drawdown((GrowthPoint(date(2024, 1, 1), 0), GrowthPoint(date(2024, 1, 2), 1)))
        self.assertEqual(flat.value, 0)
        self.assertEqual(monthly_performance(()), ())
        self.assertEqual(monthly_performance((GrowthPoint(date(2024, 1, 1), 100),)), ())
        result = monthly_performance(
            (GrowthPoint(date(2024, 1, 1), 0), GrowthPoint(date(2024, 1, 31), 100))
        )
        self.assertEqual(result[0].return_value, 0)


if __name__ == "__main__":
    unittest.main()
