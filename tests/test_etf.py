from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import requests
from streamlit.testing.v1 import AppTest
from ticker_analyzer.providers.etf import (
    EtfDataError,
    EtfHoldings,
    fetch_etf_holdings,
    fetch_public_etf_holdings,
    normalize_holdings,
    public_holdings_location,
)

PUBLIC_HTML = """
<h1>VOO Holdings Information</h1><script>private data must not be parsed</script>
<table><thead><tr><th>No.</th><th>Symbol</th><th>Name</th><th>% Weight</th><th>Shares</th></tr></thead>
<tbody><tr><td>1</td><td><a>NVDA</a></td><td>NVIDIA Corporation</td><td>8.08%</td><td>100</td></tr>
<tr><td>2</td><td>AAPL</td><td>Apple Inc.</td><td>7.03%</td><td>90</td></tr></tbody></table>
<p>As of <span>Aug 31, 2026</span></p>
"""


def fund_result():
    return EtfHoldings("VOO", pd.DataFrame({
        "Ticker": ["NVDA", "AAPL"], "Company": ["NVIDIA", "Apple"], "Weight (%)": [8.08, 7.03],
    }), "Test provider", "https://example.com/holdings", datetime(2026, 10, 4, tzinfo=UTC))


class EtfProviderTest(unittest.TestCase):
    def test_fractional_weights_are_percentages_of_whole_fund(self):
        frame = pd.DataFrame({"Name": ["Apple", "NVIDIA"], "Holding Percent": [.0703, {"raw": .0808}]}, index=pd.Index(["AAPL", "NVDA"], name="Symbol"))
        result = normalize_holdings(frame, fractions=True)
        self.assertEqual(result["Ticker"].tolist(), ["NVDA", "AAPL"])
        self.assertAlmostEqual(result["Weight (%)"].sum(), 15.11)
        self.assertAlmostEqual(result["Weight (%)"].iloc[0], 8.08)

    def test_invalid_rows_do_not_become_zero_weight_holdings(self):
        frame = pd.DataFrame({"Symbol": ["A", "B", "C", "D"], "Name": ["Valid", "Missing", "Negative", "Invalid"], "Holding Percent": [.1, None, -.1, 4]})
        result = normalize_holdings(frame, fractions=True)
        self.assertEqual(result["Ticker"].tolist(), ["A"])
        for frame in (pd.DataFrame(), pd.DataFrame({"Name": ["A"]}), pd.DataFrame({"Name": ["A", "B"], "Holding Percent": [.8, .7]})):
            with self.subTest(frame=frame), self.assertRaises(EtfDataError):
                normalize_holdings(frame, fractions=True)

    @patch("ticker_analyzer.providers.etf.yf.Ticker")
    def test_yahoo_etf_does_not_fetch_company_info(self, ticker):
        funds = ticker.return_value.funds_data
        funds.quote_type.return_value = "ETF"
        funds.top_holdings = pd.DataFrame({"Name": ["Apple"], "Holding Percent": [.07]}, index=pd.Index(["AAPL"], name="Symbol"))
        result = fetch_etf_holdings(" eunl.de ")
        self.assertEqual(result.ticker, "EUNL.DE")
        self.assertEqual(result.source, "Yahoo Finance")
        self.assertIsNone(result.as_of)

    @patch("ticker_analyzer.providers.etf.fetch_public_etf_holdings", return_value=fund_result())
    @patch("ticker_analyzer.providers.etf.yf.Ticker")
    def test_invalid_crumb_and_empty_yahoo_results_use_independent_fallback(self, ticker, public):
        funds = ticker.return_value.funds_data
        for response in (requests.HTTPError("401 Invalid Crumb"), None):
            with self.subTest(response=response):
                funds.quote_type.side_effect = response
                funds.quote_type.return_value = None
                funds.top_holdings = pd.DataFrame()
                self.assertEqual(fetch_etf_holdings("VOO").holdings["Ticker"].iloc[0], "NVDA")
        self.assertEqual(public.call_count, 2)

    @patch("ticker_analyzer.providers.etf.fetch_public_etf_holdings")
    @patch("ticker_analyzer.providers.etf.yf.Ticker")
    def test_stock_ticker_is_rejected_without_fallback(self, ticker, public):
        ticker.return_value.funds_data.quote_type.return_value = "EQUITY"
        with self.assertRaisesRegex(EtfDataError, "not classified"):
            fetch_etf_holdings("NVDA")
        public.assert_not_called()

    @patch("ticker_analyzer.providers.etf.fetch_public_etf_holdings", side_effect=EtfDataError("Unavailable"))
    @patch("ticker_analyzer.providers.etf.yf.Ticker", side_effect=requests.HTTPError("401"))
    def test_international_ticker_is_not_replaced_with_a_different_us_fund(self, ticker, public):
        with self.assertRaisesRegex(EtfDataError, "EUNL.DE"):
            fetch_etf_holdings("EUNL.DE")
        public.assert_called_once_with("EUNL.DE")
        self.assertEqual(public_holdings_location("EUNL.DE"), ("https://stockanalysis.com/quote/etr/EUNL/holdings/", "ETR:EUNL"))
        self.assertIsNone(public_holdings_location("ETF.WA"))
        self.assertIsNone(public_holdings_location(".DE"))
        with self.assertRaises(EtfDataError):
            fetch_etf_holdings("bad ticker")
        with self.assertRaises(EtfDataError):
            fetch_etf_holdings("ACC_STMT")

    @patch("ticker_analyzer.providers.etf.requests.get")
    def test_public_html_weights_and_holdings_date(self, get):
        get.return_value = SimpleNamespace(text=PUBLIC_HTML, raise_for_status=lambda: None)
        result = fetch_public_etf_holdings("VOO")
        self.assertEqual(result.as_of, "Aug 31, 2026")
        self.assertEqual(result.holdings["Weight (%)"].tolist(), [8.08, 7.03])
        self.assertTrue(result.warnings)
        self.assertIn("stockanalysis.com/etf/voo/holdings/", result.source_url)
        for html in (PUBLIC_HTML.replace("VOO Holdings", "QQQ Holdings"), "<h1>VOO Holdings Information</h1>"):
            get.return_value.text = html
            with self.assertRaises(EtfDataError):
                fetch_public_etf_holdings("VOO")

    @patch("ticker_analyzer.providers.etf.requests.get")
    @patch("ticker_analyzer.providers.etf.yf.Ticker", side_effect=requests.HTTPError("401 Invalid Crumb"))
    def test_vvsm_recovers_exact_xetra_listing_and_rejects_wrong_exchange(self, ticker, get):
        get.return_value = SimpleNamespace(text=PUBLIC_HTML.replace("VOO Holdings", "ETR:VVSM Holdings"), raise_for_status=lambda: None)
        result = fetch_etf_holdings("vvsm.de")
        self.assertEqual(result.ticker, "VVSM.DE")
        self.assertEqual(result.as_of, "Aug 31, 2026")
        self.assertEqual(result.holdings["Weight (%)"].tolist(), [8.08, 7.03])
        self.assertEqual(get.call_args.args[0], "https://stockanalysis.com/quote/etr/VVSM/holdings/")
        get.return_value.text = PUBLIC_HTML.replace("VOO Holdings", "LON:VVSM Holdings")
        with self.assertRaises(EtfDataError):
            fetch_etf_holdings("VVSM.DE")


class EtfViewTest(unittest.TestCase):
    def test_multiple_funds_keep_independent_results_errors_and_selection_after_navigation(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_tickers"] = ["NVDA"]
            app.session_state["selected_etfs"] = ["VOO", "VVSM.DE", "QQQ"]

            def fetch(ticker):
                if ticker == "QQQ":
                    raise EtfDataError("Holdings unavailable for QQQ")
                return replace(fund_result(), ticker=ticker)

            with patch("ticker_analyzer.ui.etf_view.cached_etf_holdings", side_effect=fetch):
                app.run()
                next(button for button in app.button if button.label == "Show holdings").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs[0].dataframe), 2)
            self.assertTrue(any("QQQ" in warning.value for warning in app.warning))
            self.assertEqual(app.session_state["selected_tickers"], ["NVDA"])
            self.assertEqual(set(app.session_state["etf_holdings_by_ticker"]), {"VOO", "VVSM.DE"})
            app.multiselect(key="etf_selection").set_value(["VVSM.DE"]).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.tabs[0].dataframe), 1)
            app.sidebar.radio[0].set_value("Simulation").run()
            app.sidebar.radio[0].set_value("ETF").run()
            self.assertEqual(app.multiselect(key="etf_selection").value, ["VVSM.DE"])
            self.assertEqual(len(app.tabs[0].dataframe), 1)
            app.multiselect(key="etf_selection").set_value([]).run()
            self.assertFalse(app.dataframe)
            self.assertFalse(app.exception)

    def test_suggestion_adds_an_etf_without_replacing_previous_funds_or_companies(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_tickers"] = ["NVDA"]
            with (
                patch("ticker_analyzer.ui.etf_view.st_searchbox", return_value="VVSM.DE | VanEck Semiconductor UCITS ETF"),
                patch("ticker_analyzer.ui.etf_view.cached_etf_holdings", side_effect=lambda ticker: replace(fund_result(), ticker=ticker)),
            ):
                app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_etfs"], ["VOO", "VVSM.DE"])
            self.assertEqual(app.session_state["selected_tickers"], ["NVDA"])
            self.assertEqual(len(app.tabs[0].dataframe), 2)

    def test_manual_add_accepts_multiple_symbols_and_preserves_existing_funds(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_tickers"] = []
            app.run()
            next(field for field in app.text_input if field.label == "ETF tickers").set_value("vvsm.de, QQQ; VOO")
            with patch("ticker_analyzer.ui.etf_view.cached_etf_holdings", side_effect=lambda ticker: replace(fund_result(), ticker=ticker)):
                next(button for button in app.button if button.label == "Add ETFs").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_etfs"], ["VOO", "VVSM.DE", "QQQ"])
            self.assertEqual(len(app.tabs[0].dataframe), 3)

    def test_selected_holdings_follow_display_order_and_map_known_us_share_classes(self):
        from ticker_analyzer.ui.etf_view import selected_holding_tickers
        frame = pd.DataFrame({"Ticker": ["AAPL", "BRK.B", "9988.HK", "—", "AAPL"]}, index=[9, 8, 7, 6, 5])
        self.assertEqual(selected_holding_tickers(frame, [1, 2, 0, 3, 4, 99, -1], "Stock Analysis / Finnhub"), ["BRK-B", "9988.HK", "AAPL"])
        self.assertEqual(selected_holding_tickers(frame, [], "Yahoo Finance"), [])

    def test_selected_taiwan_listings_are_analyzable_without_guessing_unknown_exchanges(self):
        from ticker_analyzer.ui.etf_view import selected_holding_tickers

        frame = pd.DataFrame({"Ticker": ["TPE: 2330", "twse:2330", "2330.TW", "UNKNOWN: 2330"]})
        self.assertEqual(selected_holding_tickers(frame, [0, 1, 2, 3], "Stock Analysis / Finnhub"), ["2330.TW"])

    def test_selected_etf_rows_add_companies_and_open_analyzer(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_tickers"] = ["NVDA"]
            app.session_state["etf_holdings"] = fund_result()
            app.run()
            add = next(button for button in app.button if button.label == "Add selected companies to Analyzer")
            self.assertTrue(add.disabled)
            app.session_state[app.dataframe[0].key] = {"selection": {"rows": [0, 1], "columns": [], "cells": []}}
            app.run()
            with patch("ticker_analyzer.ui.analysis_actions.analyze_selected_tickers", return_value=({}, {})):
                next(button for button in app.button if button.label == "Add selected companies to Analyzer").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_tickers"], ["NVDA", "AAPL"])
            self.assertEqual(app.sidebar.radio[0].value, "Stock Analyzer")
            self.assertTrue(app.session_state["analysis_pending_changes"])

    def test_etf_lookup_is_independent_and_does_not_keep_old_result_after_failure(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_tickers"] = ["NVDA"]
            with (
                patch("ticker_analyzer.ui.etf_view.cached_etf_holdings", return_value=fund_result()) as fetch,
                patch("ticker_analyzer.ui.analysis_actions.analyze_selected_tickers") as analyze,
            ):
                app.run()
                fetch.assert_not_called()
                next(button for button in app.button if button.label == "Show holdings").click().run()
                analyze.assert_not_called()
            self.assertFalse(app.exception)
            self.assertEqual(app.dataframe[0].value["Ticker"].tolist(), ["NVDA", "AAPL"])
            self.assertEqual(next(metric for metric in app.metric if metric.label == "Share of the fund shown").value, "15.11%")
            self.assertEqual(app.session_state["selected_tickers"], ["NVDA"])
            app.multiselect(key="etf_selection").set_value(["VOO"])
            with patch("ticker_analyzer.ui.etf_view.cached_etf_holdings", side_effect=EtfDataError("Unavailable")):
                next(button for button in app.button if button.label == "Show holdings").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.dataframe)
            self.assertTrue(any(warning.value == "Unavailable" for warning in app.warning))

    def test_failed_fetch_is_not_cached(self):
        from ticker_analyzer.ui.etf_view import cached_etf_holdings
        cached_etf_holdings.clear()
        with patch("ticker_analyzer.ui.etf_view.fetch_etf_holdings", side_effect=[EtfDataError("Unavailable"), fund_result()]) as fetch:
            with self.assertRaises(EtfDataError):
                cached_etf_holdings("VOO")
            self.assertEqual(cached_etf_holdings("VOO").ticker, "VOO")
            self.assertEqual(fetch.call_count, 2)
        cached_etf_holdings.clear()
