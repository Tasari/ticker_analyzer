from __future__ import annotations

import unittest
from datetime import date

import pandas as pd
from ticker_analyzer.simulation import SimulationError, simulate_buy_and_hold


class SimulationTest(unittest.TestCase):
    def test_equal_weight_buy_and_hold_uses_fractional_shares(self):
        dates = pd.to_datetime(["2024-01-01", "2024-12-31"])
        result = simulate_buy_and_hold(
            {
                "DOUBLE": pd.Series([100.0, 200.0], index=dates),
                "FLAT": pd.Series([50.0, 50.0], index=dates),
            },
            {"DOUBLE": 0.5, "FLAT": 0.5},
            10_000,
            date(2024, 1, 1),
            date(2024, 12, 31),
        )

        self.assertAlmostEqual(result.final_value, 15_000)
        self.assertAlmostEqual(result.return_value, 0.5)
        self.assertAlmostEqual(result.positions[0].shares, 50)
        self.assertAlmostEqual(result.positions[1].shares, 100)

    def test_late_listing_keeps_allocation_in_cash_until_first_price(self):
        result = simulate_buy_and_hold(
            {
                "LATE": pd.Series(
                    [20.0, 40.0],
                    index=pd.to_datetime(["2024-07-01", "2024-12-31"]),
                ),
                "MISSING": pd.Series(dtype=float),
            },
            {"LATE": 0.5, "MISSING": 0.5},
            10_000,
            date(2024, 1, 1),
            date(2024, 12, 31),
        )

        self.assertEqual(result.position_values.loc["2024-06-28", "LATE"], 5_000)
        self.assertEqual(result.positions[0].entry_date, date(2024, 7, 1))
        self.assertEqual(result.positions[1].status, "Cash: no usable prices")
        self.assertAlmostEqual(result.final_value, 15_000)

    def test_rejects_invalid_dates_capital_and_weights(self):
        with self.assertRaisesRegex(SimulationError, "positive"):
            simulate_buy_and_hold({}, {"A": 1.0}, 0, date(2024, 1, 1), date(2024, 2, 1))
        with self.assertRaisesRegex(SimulationError, "after"):
            simulate_buy_and_hold({}, {"A": 1.0}, 1, date(2024, 2, 1), date(2024, 1, 1))
        with self.assertRaisesRegex(SimulationError, "100%"):
            simulate_buy_and_hold({}, {"A": 0.8}, 1, date(2024, 1, 1), date(2024, 2, 1))
        with self.assertRaisesRegex(SimulationError, "at least one"):
            simulate_buy_and_hold({}, {}, 1, date(2024, 1, 1), date(2024, 2, 1))
        with self.assertRaisesRegex(SimulationError, "negative"):
            simulate_buy_and_hold({}, {"A": 1.1, "B": -0.1}, 1, date(2024, 1, 1), date(2024, 2, 1))


if __name__ == "__main__":
    unittest.main()
