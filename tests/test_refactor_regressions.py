from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pandas as pd
from ticker_analyzer.file_io import write_json_atomic
from ticker_analyzer.metrics.formulas import ohlson_probability
from ticker_analyzer.portfolio.advanced_simulation import SimulationAssumptions, simulate_strategies
from ticker_analyzer.portfolio.simulation import SimulationError, simulate_buy_and_hold
from ticker_analyzer.ranking.storage import load_ranking, save_ranking


class PersistenceRegressionTest(unittest.TestCase):
    def test_failed_writes_preserve_original_and_remove_staging_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            write_json_atomic({"original": True}, path)
            original = path.read_bytes()
            with self.assertRaises(TypeError):
                write_json_atomic({"unsupported": object()}, path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.iterdir()), [path])
            with patch("ticker_analyzer.file_io.os.replace", side_effect=OSError("locked")):
                with self.assertRaisesRegex(OSError, "locked"):
                    write_json_atomic({"new": True}, path, durable=True)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_concurrent_writers_stage_independently_and_publish_complete_json(self):
        import os

        barrier = Barrier(2)
        actual_replace = os.replace
        actual_dump = json.dump
        staged = []

        def stage(*args, **kwargs):
            actual_dump(*args, **kwargs)
            barrier.wait(timeout=5)

        def publish(source, target):
            staged.append(source)
            actual_replace(source, target)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            payloads = [{"writer": index, "rows": list(range(100))} for index in range(2)]
            with (
                patch("ticker_analyzer.file_io.os.replace", side_effect=publish),
                patch("ticker_analyzer.file_io.json.dump", side_effect=stage),
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(write_json_atomic, payload, path) for payload in payloads]
                    for future in futures:
                        future.result(timeout=10)
            self.assertEqual(len(set(staged)), 2)
            self.assertIn(json.loads(path.read_text(encoding="utf-8")), payloads)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_all_three_rankings_remain_cached_across_reruns(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{asset}.json" for asset in ("stocks", "etfs", "crypto")]
            for path in paths:
                save_ranking({"metadata": {}, "companies": [], "errors": []}, path)
            with patch("ticker_analyzer.ranking.storage.json.load", wraps=json.load) as parse:
                for _ in range(3):
                    for path in paths:
                        self.assertEqual(load_ranking(path)["companies"], [])
            self.assertEqual(parse.call_count, 3)


class NumericalRegressionTest(unittest.TestCase):
    def test_simulations_reject_nonfinite_capital_allocations_and_assumptions(self):
        start, end = date(2024, 1, 1), date(2024, 3, 1)
        for value in (float("nan"), float("inf"), float("-inf")):
            for capital, weights in ((value, {"A": 1.0}), (1000, {"A": value})):
                with self.subTest(value=value, capital=capital):
                    with self.assertRaises(SimulationError):
                        simulate_buy_and_hold({}, weights, capital, start, end)
                    with self.assertRaises(SimulationError):
                        simulate_strategies({}, {}, weights, capital, start, end, SimulationAssumptions())
            for field in (
                "contribution_amount",
                "commission_percent",
                "commission_fixed",
                "spread_percent",
                "capital_gains_tax_percent",
                "dividend_tax_percent",
                "annual_inflation_percent",
                "annual_risk_free_rate_percent",
                "cash_weight",
            ):
                with self.subTest(value=value, field=field):
                    assumptions = replace(SimulationAssumptions(), **{field: value})
                    with self.assertRaises(SimulationError):
                        simulate_strategies({}, {}, {"A": 1.0}, 1000, start, end, assumptions)

    def test_shared_market_preparation_keeps_input_and_strategy_outputs_independent(self):
        prices = pd.Series([100.0, 200.0, 150.0], index=pd.date_range("2024-01-01", periods=3, freq="MS"))
        dividends = pd.Series([1.0], index=prices.index[1:2])
        original_prices, original_dividends = prices.copy(), dividends.copy()
        comparison = simulate_strategies(
            {"A": prices},
            {"A": dividends},
            {"A": 1.0},
            1000,
            date(2024, 1, 1),
            date(2024, 3, 1),
            SimulationAssumptions(rebalance_frequency="monthly"),
        )
        pd.testing.assert_series_equal(prices, original_prices)
        pd.testing.assert_series_equal(dividends, original_dividends)
        assert comparison.rebalanced is not None
        rebalanced_correlation = comparison.rebalanced.correlation_matrix.copy()
        comparison.buy_and_hold.correlation_matrix.iloc[0, 0] = 123
        pd.testing.assert_frame_equal(comparison.rebalanced.correlation_matrix, rebalanced_correlation)

    def test_ohlson_handles_zero_income_and_extreme_logistic_without_crashing_analysis(self):
        dates = pd.to_datetime(["2023-12-31", "2024-12-31"])
        balance = pd.DataFrame(
            {dates[-1]: {"Total Assets": 100, "Total Liab": 50, "Current Assets": 20, "Current Liabilities": 10}}
        )
        income = pd.DataFrame([[0.0, 0.0]], index=["Net Income"], columns=dates)
        cashflow = pd.DataFrame({dates[-1]: {"Operating Cash Flow": 10.0}})
        self.assertIsNone(ohlson_probability(income, balance, cashflow))
        income.loc["Net Income"] = [1.0, 1.0]
        cashflow.loc["Operating Cash Flow"] = 1e300
        self.assertEqual(ohlson_probability(income, balance, cashflow), 0.0)


if __name__ == "__main__":
    unittest.main()
