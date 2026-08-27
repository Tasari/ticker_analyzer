from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from ticker_analyzer.portfolio.returns import ReturnsTable
from ticker_analyzer.ui.simulation_view import (
    _account_statement_prices,
    _cached_fx_factor,
    _convert_to_base_currency,
    _fetch_simulation_histories,
    _fetch_simulation_market_data,
    _simulation_history_start,
)


class SimulationViewTest(unittest.TestCase):
    def test_history_fetch_extends_to_five_year_window_without_changing_selection(self):
        self.assertEqual(
            _simulation_history_start(date(2024, 1, 1), date(2025, 12, 31)),
            date(2020, 12, 24),
        )
        self.assertEqual(
            _simulation_history_start(date(2018, 1, 1), date(2025, 12, 31)),
            date(2018, 1, 1),
        )

    def test_account_statement_pseudo_ticker_uses_imported_monthly_returns(self):
        table = ReturnsTable({(2024, 1): 0.10, (2024, 2): -0.05})

        prices = _account_statement_prices(table, date(2024, 1, 1), date(2024, 2, 29))

        self.assertEqual(prices.index[0], pd.Timestamp("2024-01-01"))
        self.assertAlmostEqual(prices.iloc[-1], 104.5)

    def test_account_statement_history_is_clamped_to_available_months(self):
        table = ReturnsTable({(2024, 1): 0.10, (2024, 2): -0.05})

        prices = _account_statement_prices(table, date(2019, 1, 1), date(2025, 1, 1))

        self.assertEqual(prices.index[0], pd.Timestamp("2024-01-01"))
        self.assertEqual(prices.index[-1], pd.Timestamp("2024-02-29"))
        self.assertAlmostEqual(prices.iloc[-1], 104.5)

    def test_account_statement_pseudo_ticker_does_not_fetch_yahoo_prices(self):
        table = ReturnsTable({(2024, 1): 0.10})
        with patch(
            "ticker_analyzer.ui.simulation_view._cached_adjusted_prices",
            side_effect=AssertionError("Yahoo should not be called"),
        ):
            histories, warnings = _fetch_simulation_histories(
                {},
                date(2024, 1, 1),
                date(2024, 1, 31),
                "USD",
                account_returns=table,
            )

        self.assertEqual(list(histories), ["ACC_STMT"])
        self.assertAlmostEqual(histories["ACC_STMT"].iloc[-1], 110)
        self.assertEqual(warnings, [])

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

    def test_market_data_fetch_keeps_prices_and_dividends_separate(self):
        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        prices = pd.Series([100.0, 102.0], index=index)
        dividends = pd.Series([1.5], index=index[1:])
        with patch(
            "ticker_analyzer.ui.simulation_view._cached_market_history",
            return_value=(prices, dividends),
        ):
            fetched_prices, fetched_dividends, warnings = _fetch_simulation_market_data(
                {"A": {"currency": "USD"}},
                date(2024, 1, 1),
                date(2024, 1, 3),
                "USD",
            )

        self.assertEqual(list(fetched_prices["A"]), [100, 102])
        self.assertEqual(list(fetched_dividends["A"]), [1.5])
        self.assertEqual(warnings, [])

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

    def test_fx_conversion_uses_inverse_pair_when_direct_pair_is_unavailable(self):
        inverse = pd.Series([4.0], index=pd.to_datetime(["2024-01-02"]))
        with patch(
            "ticker_analyzer.ui.simulation_view._try_fx_history",
            side_effect=[pd.Series(dtype=float), inverse],
        ):
            factor = _cached_fx_factor.__wrapped__(
                "PLN",
                "USD",
                date(2024, 1, 1),
                date(2024, 1, 2),
            )

        self.assertEqual(list(factor), [0.25])


if __name__ == "__main__":
    unittest.main()
