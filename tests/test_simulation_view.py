from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from ticker_analyzer.ui.simulation_view import _convert_to_base_currency


class SimulationViewTest(unittest.TestCase):
    def test_converts_prices_with_historical_exchange_rate(self):
        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        prices = pd.Series([100.0, 110.0], index=index)
        factor = pd.Series([1.2, 1.25], index=index)

        with patch(
            "ticker_analyzer.ui.simulation_view._cached_fx_factor",
            return_value=factor,
        ):
            converted = _convert_to_base_currency(
                prices,
                "EUR",
                "USD",
                date(2024, 1, 1),
                date(2024, 1, 3),
            )

        self.assertEqual(list(converted), [120.0, 137.5])

    def test_converts_london_pence_to_pounds_before_base_conversion(self):
        prices = pd.Series([250.0], index=pd.to_datetime(["2024-01-02"]))

        converted = _convert_to_base_currency(
            prices,
            "GBp",
            "GBP",
            date(2024, 1, 1),
            date(2024, 1, 2),
        )

        self.assertEqual(list(converted), [2.5])


if __name__ == "__main__":
    unittest.main()
