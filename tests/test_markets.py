from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.markets import (
    MARKET_SUFFIXES,
    XTB_EXCHANGE_MARKETS,
    canonical_currency,
    convention_for_ticker,
    currency_convention,
    normalize_price_history,
    normalize_price_targets,
    normalize_quote_info,
)


class MarketConventionsTest(unittest.TestCase):
    def test_supported_market_metadata_comes_from_ticker_suffix(self):
        london = convention_for_ticker("BGEO.L")
        warsaw = convention_for_ticker("PKN.WA")

        self.assertEqual((london.market, london.country, london.currency), ("London Stock Exchange", "United Kingdom", "GBP"))
        self.assertEqual((warsaw.market, warsaw.timezone), ("Warsaw Stock Exchange", "Europe/Warsaw"))
        self.assertEqual(MARKET_SUFFIXES["Japan (Tokyo)"], ".T")
        self.assertEqual(XTB_EXCHANGE_MARKETS["Xetra"], ("germany", "Germany", ".DE"))

    def test_minor_currency_is_converted_to_major_currency(self):
        self.assertEqual(currency_convention("GBp"), ("GBP", 0.01))
        self.assertEqual(currency_convention("GBP"), ("GBP", 1.0))
        self.assertEqual(canonical_currency("", "PKN.WA"), "PLN")

    def test_quote_info_prices_are_normalized_without_scaling_fundamentals(self):
        normalized = normalize_quote_info(
            {
                "currency": "GBp",
                "currentPrice": 13_620,
                "targetMeanPrice": 15_000,
                "marketCap": 5_000_000,
                "market": "gb_market",
            },
            "BGEO.L",
        )

        self.assertEqual(normalized["currency"], "GBP")
        self.assertEqual(normalized["quoteCurrency"], "GBp")
        self.assertEqual(normalized["currentPrice"], 136.2)
        self.assertEqual(normalized["targetMeanPrice"], 150.0)
        self.assertEqual(normalized["marketCap"], 5_000_000)
        self.assertEqual(normalized["market"], "London Stock Exchange")
        self.assertEqual(normalized["providerMarket"], "gb_market")
        self.assertEqual(normalize_quote_info(normalized, "BGEO.L")["currentPrice"], 136.2)

    def test_price_history_normalizes_ohlc_but_not_dividends(self):
        history = pd.DataFrame({"Open": [13_500], "Close": [13_620], "Dividends": [1.2]})

        normalized = normalize_price_history(history, "GBp")

        self.assertEqual(normalized["Open"].iloc[0], 135.0)
        self.assertEqual(normalized["Close"].iloc[0], 136.2)
        self.assertEqual(normalized["Dividends"].iloc[0], 1.2)

    def test_price_targets_leave_non_price_counts_unchanged(self):
        normalized = normalize_price_targets({"mean": 15_000, "numberOfAnalystOpinions": 12}, "GBp")

        self.assertEqual(normalized, {"mean": 150.0, "numberOfAnalystOpinions": 12})


if __name__ == "__main__":
    unittest.main()
