from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests
from ticker_analyzer.providers.fx import _cached_history, exchange_rate, fx_history, rates_on_dates


def response(rate):
    return Mock(json=Mock(return_value={"chart": {"result": [{
        "timestamp": [1704412800], "indicators": {"quote": [{"close": [rate]}]},
    }]}}))


class FxTests(unittest.TestCase):
    def setUp(self):
        _cached_history.cache_clear()

    @patch("ticker_analyzer.providers.fx.requests.get")
    def test_direct_rate_is_shared_between_companies_and_copy_is_independent(self, get):
        get.return_value = response(4.)
        first = fx_history("USD", "PLN", "2024-01-01", "2024-02-01")
        first.iloc[0] = 99
        second = fx_history("USD", "PLN", "2024-06-01", "2024-07-01")
        self.assertEqual(second.iloc[0], 4.)
        self.assertEqual(get.call_count, 1)

    @patch("ticker_analyzer.providers.fx.requests.get")
    def test_inverse_and_failed_requests(self, get):
        get.side_effect = [requests.RequestException(), response(.25)]
        self.assertEqual(fx_history("USD", "PLN", "2024-01-01", "2024-02-01").iloc[0], 4.)
        _cached_history.cache_clear()
        get.side_effect = [requests.RequestException(), requests.RequestException()]
        self.assertTrue(fx_history("USD", "PLN", "2024-01-01", "2024-02-01").empty)
        self.assertTrue(fx_history("USD", "PLN", "2024-01-01", "2024-02-01").empty)
        self.assertEqual(get.call_count, 4)

    @patch("ticker_analyzer.providers.fx.fx_history")
    def test_rates_never_use_future_or_stale_observations(self, history):
        history.return_value = pd.Series([4.], index=pd.to_datetime(["2024-01-05"]))
        dates = pd.to_datetime(["2024-01-04", "2024-01-07", "2024-01-20"])
        actual = rates_on_dates("USD", "PLN", dates)
        self.assertTrue(pd.isna(actual.iloc[0]))
        self.assertEqual(actual.iloc[1], 4.)
        self.assertTrue(pd.isna(actual.iloc[2]))

    def test_same_currency_and_unknown_currency_do_not_fetch(self):
        self.assertEqual(exchange_rate("USD", "USD", "2024-01-01"), 1.)
        self.assertIsNone(exchange_rate("", "USD", "2024-01-01"))
        self.assertEqual(len(fx_history("USD", "USD", "2024-01-01", "2024-01-02")), 2)
        self.assertTrue(fx_history("", "USD", "2024-01-01", "2024-01-02").empty)

    @patch("ticker_analyzer.providers.fx._download_history")
    def test_sparse_direct_cross_is_completed_through_usd(self, download):
        days = pd.to_datetime(["2024-01-05", "2024-01-08"])
        download.side_effect = [
            pd.Series([3.6], index=days[1:]),
            pd.Series([1.2, 1.3], index=days),
            pd.Series([2.5, 2.7], index=days),
        ]
        actual = fx_history("GBP", "GEL", "2024-01-01", "2024-12-31")
        self.assertEqual(actual.tolist(), [3.0, 3.6])
