from __future__ import annotations

import unittest
from unittest.mock import patch

from ticker_analyzer.ranking.currencies import usd_amount_fields, usd_market_cap
from ticker_analyzer.ranking.filters import RankingFilters, filter_ranking_companies


class RankingCurrencyTests(unittest.TestCase):
    @patch("ticker_analyzer.ranking.currencies.exchange_rate", return_value=.25)
    def test_original_amount_is_retained_and_filter_uses_usd(self, rate):
        fields = usd_amount_fields("market_cap", 40_000_000_000, "PLN")
        self.assertEqual(fields["market_cap"], 10_000_000_000)
        self.assertEqual(fields["market_cap_original"], 40_000_000_000)
        self.assertEqual(fields["market_cap_original_currency"], "PLN")
        self.assertEqual(fields["market_cap_currency"], "USD")
        row = {"ticker": "PKN.WA", **fields}
        self.assertEqual(filter_ranking_companies([row], RankingFilters(minimum_market_cap=15_000_000_000)), [])

    @patch("ticker_analyzer.ranking.currencies.exchange_rate", return_value=None)
    def test_missing_fx_keeps_original_without_comparing_incompatible_numbers(self, rate):
        fields = usd_amount_fields("market_cap", 40_000_000_000, "PLN")
        self.assertIsNone(fields["market_cap"])
        self.assertIsNone(usd_market_cap({"market_cap": 99_000_000_000}))
        self.assertEqual(fields["market_cap_original"], 40_000_000_000)
