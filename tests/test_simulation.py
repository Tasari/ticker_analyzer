from __future__ import annotations

import unittest
from datetime import date

import pandas as pd
from ticker_analyzer.portfolio.simulation import (
    TRAILING_RETURN_PERIODS,
    SimulationError,
    simulate_buy_and_hold,
)


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

    def test_different_exchange_calendars_are_forward_filled_without_reallocation(self):
        result = simulate_buy_and_hold(
            {
                "US": pd.Series(
                    [100.0, 110.0],
                    index=pd.to_datetime(["2024-01-02", "2024-01-04"]),
                ),
                "EU": pd.Series(
                    [200.0, 180.0],
                    index=pd.to_datetime(["2024-01-03", "2024-01-05"]),
                ),
            },
            {"US": 0.5, "EU": 0.5},
            10_000,
            date(2024, 1, 1),
            date(2024, 1, 5),
        )

        self.assertEqual(result.position_values.loc["2024-01-02", "EU"], 5_000)
        self.assertAlmostEqual(result.position_values.loc["2024-01-05", "US"], 5_500)
        self.assertAlmostEqual(result.position_values.loc["2024-01-05", "EU"], 4_500)
        self.assertAlmostEqual(result.final_value, 10_000)

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

    def test_trailing_returns_use_history_outside_selected_chart_range(self):
        dates = pd.to_datetime(["2019-12-31", "2023-12-29", "2024-06-28", "2024-09-30", "2024-11-29", "2024-12-31"])
        result = simulate_buy_and_hold(
            {"A": pd.Series([50.0, 80.0, 100.0, 110.0, 120.0, 132.0], index=dates)},
            {"A": 1.0},
            10_000,
            date(2024, 6, 28),
            date(2024, 12, 31),
        )

        self.assertEqual(result.portfolio_values.index[0], pd.Timestamp("2024-06-28"))
        returns = dict(result.positions[0].trailing_returns)
        self.assertAlmostEqual(returns["1M"], 0.10)
        self.assertAlmostEqual(returns["3M"], 0.20)
        self.assertAlmostEqual(returns["6M"], 0.32)
        self.assertAlmostEqual(returns["1Y"], 0.65)
        self.assertAlmostEqual(returns["5Y"], 1.64)
        self.assertEqual(tuple(returns), tuple(label for label, _ in TRAILING_RETURN_PERIODS))

    def test_trailing_return_is_unavailable_without_full_period_history(self):
        result = simulate_buy_and_hold(
            {"NEW": pd.Series([100.0, 120.0], index=pd.to_datetime(["2024-07-01", "2024-12-31"]))},
            {"NEW": 1.0},
            10_000,
            date(2024, 7, 1),
            date(2024, 12, 31),
        )

        returns = dict(result.positions[0].trailing_returns)
        self.assertIsNone(returns["1Y"])
        self.assertIsNone(returns["5Y"])


if __name__ == "__main__":
    unittest.main()
