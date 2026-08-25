from __future__ import annotations

import unittest

from ticker_analyzer.ticker_symbols import normalize_ticker, ticker_for_market


class TickerSymbolsTest(unittest.TestCase):
    def test_normalizes_symbols_used_by_multiple_markets(self):
        self.assertEqual(normalize_ticker(" pkn.wa "), "PKN.WA")
        self.assertEqual(normalize_ticker("brk/b"), "BRK-B")
        self.assertEqual(normalize_ticker("9988.hk"), "9988.HK")
        self.assertIsNone(normalize_ticker("bad ticker"))

    def test_builds_yahoo_ticker_from_selected_market(self):
        self.assertEqual(ticker_for_market("PKN", "Poland (Warsaw)"), "PKN.WA")
        self.assertEqual(ticker_for_market("LLOY", "United Kingdom (London)"), "LLOY.L")
        self.assertEqual(ticker_for_market("VOW3.DE", "Germany (Xetra)"), "VOW3.DE")
        self.assertEqual(ticker_for_market("9988", "Hong Kong"), "9988.HK")
        self.assertEqual(ticker_for_market("FUTU", "United States / ADR"), "FUTU")


if __name__ == "__main__":
    unittest.main()
