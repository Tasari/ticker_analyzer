from __future__ import annotations

import unittest
from datetime import date

import pandas as pd
from ticker_analyzer.portfolio.advanced_simulation import (
    SimulationAssumptions,
    simulate_strategies,
)
from ticker_analyzer.portfolio.simulation import SimulationError


class AdvancedSimulationTest(unittest.TestCase):
    def test_monthly_contributions_are_invested_without_distorting_twr(self):
        dates = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"])
        result = simulate_strategies(
            {"FLAT": pd.Series([100.0] * 4, index=dates)},
            {},
            {"FLAT": 1.0},
            1_000,
            date(2024, 1, 1),
            date(2024, 4, 1),
            SimulationAssumptions(contribution_amount=100),
        ).buy_and_hold

        self.assertEqual(result.total_contributions, 1_300)
        self.assertEqual(result.final_value, 1_300)
        self.assertAlmostEqual(result.time_weighted_return, 0)
        self.assertEqual(result.positions[0].shares, 13)

    def test_rebalanced_strategy_is_compared_with_buy_and_hold(self):
        dates = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"])
        comparison = simulate_strategies(
            {
                "FAST": pd.Series([100.0, 200.0, 400.0, 400.0], index=dates),
                "FLAT": pd.Series([100.0] * 4, index=dates),
            },
            {},
            {"FAST": 0.5, "FLAT": 0.5},
            1_000,
            date(2024, 1, 1),
            date(2024, 4, 1),
            SimulationAssumptions(rebalance_frequency="monthly"),
        )

        self.assertEqual(comparison.buy_and_hold.final_value, 2_500)
        self.assertIsNotNone(comparison.rebalanced)
        assert comparison.rebalanced is not None
        self.assertEqual(comparison.rebalanced.final_value, 2_250)
        self.assertEqual(comparison.rebalanced.rebalance_count, 2)

    def test_dividends_can_be_reinvested_or_kept_as_cash(self):
        prices = pd.Series(
            [100.0, 100.0, 120.0],
            index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
        )
        dividends = {"A": pd.Series([10.0], index=pd.to_datetime(["2024-02-01"]))}
        common = ({"A": prices}, dividends, {"A": 1.0}, 1_000, date(2024, 1, 1), date(2024, 3, 1))

        cash = simulate_strategies(*common, SimulationAssumptions(dividend_policy="cash")).buy_and_hold
        reinvested = simulate_strategies(
            *common,
            SimulationAssumptions(dividend_policy="reinvest"),
        ).buy_and_hold

        self.assertEqual(cash.final_value, 1_300)
        self.assertEqual(cash.cash_values.iloc[-1], 100)
        self.assertEqual(reinvested.final_value, 1_320)
        self.assertEqual(reinvested.positions[0].shares, 11)
        self.assertEqual(reinvested.dividends_received, 100)

    def test_cash_allocation_and_inflation_are_reported(self):
        result = simulate_strategies(
            {"A": pd.Series([100.0, 200.0], index=pd.to_datetime(["2024-01-01", "2025-01-01"]))},
            {},
            {"A": 0.8},
            1_000,
            date(2024, 1, 1),
            date(2025, 1, 1),
            SimulationAssumptions(cash_weight=0.2, annual_inflation_percent=10),
        ).buy_and_hold

        self.assertEqual(result.final_value, 1_800)
        self.assertEqual(result.cash_values.iloc[-1], 200)
        self.assertLess(result.real_final_value, result.final_value)
        self.assertLess(result.real_time_weighted_return, result.time_weighted_return)

    def test_rebalancing_applies_fees_spread_and_realized_gain_tax(self):
        dates = pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"])
        comparison = simulate_strategies(
            {
                "UP": pd.Series([100.0, 200.0, 220.0], index=dates),
                "FLAT": pd.Series([100.0, 100.0, 100.0], index=dates),
            },
            {},
            {"UP": 0.5, "FLAT": 0.5},
            10_000,
            date(2024, 1, 1),
            date(2024, 3, 1),
            SimulationAssumptions(
                rebalance_frequency="monthly",
                commission_percent=0.2,
                commission_fixed=1,
                spread_percent=0.1,
                capital_gains_tax_percent=19,
            ),
        )

        assert comparison.rebalanced is not None
        self.assertGreater(comparison.rebalanced.fees_paid, comparison.buy_and_hold.fees_paid)
        self.assertGreater(comparison.rebalanced.taxes_paid, 0)
        self.assertLess(comparison.rebalanced.final_value, comparison.buy_and_hold.final_value)

    def test_weights_and_cash_must_total_one_hundred_percent(self):
        with self.assertRaisesRegex(SimulationError, "plus cash"):
            simulate_strategies(
                {},
                {},
                {"A": 0.7},
                1_000,
                date(2024, 1, 1),
                date(2024, 2, 1),
                SimulationAssumptions(cash_weight=0.2),
            )

    def test_portfolio_can_be_one_hundred_percent_cash(self):
        result = simulate_strategies(
            {},
            {},
            {"A": 0.0},
            1_000,
            date(2024, 1, 1),
            date(2024, 2, 1),
            SimulationAssumptions(cash_weight=1.0),
        ).buy_and_hold

        self.assertEqual(result.final_value, 1_000)
        self.assertEqual(result.cash_values.iloc[-1], 1_000)


if __name__ == "__main__":
    unittest.main()
