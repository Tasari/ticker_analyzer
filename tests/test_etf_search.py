from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ticker_analyzer.ui.analysis_actions import cached_ticker_search
from ticker_analyzer.ui.etf_actions import search_etfs


class EtfSearchTest(unittest.TestCase):
    @patch("ticker_analyzer.ui.etf_actions.cached_ticker_search", return_value=[])
    @patch("ticker_analyzer.ui.etf_actions.load_ranking")
    def test_local_ranking_keeps_named_suggestions_during_provider_outage(self, ranking, yahoo):
        ranking.return_value = {"companies": [{"ticker": "VVSM.DE", "name": "VanEck Semiconductor UCITS ETF", "market": "Xetra"}]}
        self.assertEqual(search_etfs("vvsm"), ["VVSM.DE | VanEck Semiconductor UCITS ETF | Xetra", "VVSM | Add exact ETF ticker"])
        self.assertEqual(search_etfs("semiconductor"), ["VVSM.DE | VanEck Semiconductor UCITS ETF | Xetra"])
        self.assertEqual(search_etfs("vvsm.de")[0], "VVSM.DE | VanEck Semiconductor UCITS ETF | Xetra")
        yahoo.assert_called_with("vvsm.de", quote_type="ETF")

    @patch("ticker_analyzer.ui.etf_actions.cached_ticker_search", return_value=["VVSM.DE | VanEck | Xetra"])
    @patch("ticker_analyzer.ui.etf_actions.load_ranking", side_effect=ValueError("Broken snapshot"))
    def test_remote_suggestions_and_exact_symbol_work_without_ranking(self, ranking, yahoo):
        self.assertEqual(search_etfs("vvsm.de"), ["VVSM.DE | VanEck | Xetra"])
        self.assertEqual(search_etfs(""), [])
        self.assertEqual(yahoo.call_count, 1)

    @patch("yfinance.Search")
    def test_etf_search_excludes_equities_and_keeps_full_exchange_suffix(self, search):
        search.return_value = SimpleNamespace(quotes=[
            {"symbol": "NVDA", "quoteType": "EQUITY"},
            {"symbol": "VVSM.DE", "quoteType": "ETF", "longname": "VanEck", "exchDisp": "Xetra"},
        ])
        cached_ticker_search.clear()
        self.assertEqual(cached_ticker_search("van", quote_type="ETF"), ["VVSM.DE | VanEck | Xetra"])
        self.assertEqual(len(cached_ticker_search("van")), 2)
        cached_ticker_search.clear()
