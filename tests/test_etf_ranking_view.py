from __future__ import annotations

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class EtfRankingViewTest(unittest.TestCase):
    def test_multiple_ranking_selections_open_etf_view_and_filtering_clears_old_rows(self):
        payload = {"metadata": {}, "companies": [
            {"ticker": "VVSM.DE", "name": "VanEck Semiconductor", "market": "Xetra", "overall_score": 90},
            {"ticker": "EUNL.DE", "name": "iShares Core MSCI World", "market": "Xetra", "overall_score": 80},
        ], "errors": []}
        app = AppTest.from_string(
            "from ticker_analyzer.ui.market_ranking_view import render_etf_ranking\nrender_etf_ranking()"
        )
        app.session_state["selected_etfs"] = ["VOO"]
        app.session_state["selected_tickers"] = ["NVDA"]
        with patch("ticker_analyzer.ui.market_ranking_view.load_ranking", return_value=payload):
            app.run()
            original_key = app.dataframe[0].key
            app.session_state[original_key] = {"selection": {"rows": [1, 0], "columns": [], "cells": []}}
            app.run()
            self.assertFalse(next(button for button in app.button if button.label == "Show selected ETFs").disabled)
            app.text_input(key="etf_ranking_search").set_value("EUNL").run()
            self.assertNotEqual(app.dataframe[0].key, original_key)
            self.assertTrue(next(button for button in app.button if button.label == "Show selected ETFs").disabled)
            app.session_state[app.dataframe[0].key] = {"selection": {"rows": [0], "columns": [], "cells": []}}
            app.run()
            next(button for button in app.button if button.label == "Show selected ETFs").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["page"], "ETF")
        self.assertEqual(app.session_state["selected_etfs"], ["VOO", "EUNL.DE"])
        self.assertEqual(app.session_state["selected_tickers"], ["NVDA"])
        self.assertTrue(app.session_state["etf_load_requested"])

    def test_ranking_adds_all_selected_funds_and_skips_duplicates(self):
        payload = {"metadata": {}, "companies": [
            {"ticker": "VVSM.DE", "name": "Semiconductor", "overall_score": 90},
            {"ticker": "VOO", "name": "S&P 500", "overall_score": 80},
        ], "errors": []}
        app = AppTest.from_string(
            "from ticker_analyzer.ui.market_ranking_view import render_etf_ranking\nrender_etf_ranking()"
        )
        app.session_state["selected_etfs"] = ["VOO"]
        with patch("ticker_analyzer.ui.market_ranking_view.load_ranking", return_value=payload):
            app.run()
            app.session_state[app.dataframe[0].key] = {"selection": {"rows": [0, 1], "columns": [], "cells": []}}
            app.run()
            next(button for button in app.button if button.label == "Show selected ETFs").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_etfs"], ["VOO", "VVSM.DE"])
