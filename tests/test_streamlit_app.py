from __future__ import annotations

import sys
import textwrap
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest
from tests.test_account_statement import statement_workbook
from ticker_analyzer.portfolio.returns import (
    ACCOUNT_RETURNS_STATE_KEY,
    ACCOUNT_STATEMENT_NAME_STATE_KEY,
    ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY,
    ACCOUNT_STATEMENT_TICKER,
    ReturnsTable,
)


class StreamlitAppTest(unittest.TestCase):
    def setUp(self):
        self.browser_storage = patch.dict(
            "os.environ",
            {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"},
        )
        self.browser_storage.start()

    def tearDown(self):
        self.browser_storage.stop()

    def test_site_is_locked_before_application_state_is_loaded(self):
        app = AppTest.from_file("app.py", default_timeout=10).run()

        self.assertFalse(app.exception)
        self.assertEqual([field.label for field in app.text_input], ["Password"])
        self.assertTrue(any(button.label == "Unlock" for button in app.button))
        self.assertTrue(any("not personalized" in element.value for element in app.warning))
        self.assertEqual(
            [checkbox.label for checkbox in app.checkbox],
            ["I have read and understand the disclaimer."],
        )
        self.assertTrue(next(button for button in app.button if button.label == "Unlock").disabled)
        self.assertFalse(app.sidebar.radio)

    def test_first_render_does_not_block_on_browser_storage(self):
        imported_persistence = sys.modules.pop("ticker_analyzer.persistence", None)
        try:
            with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": ""}):
                app = AppTest.from_file("app.py", default_timeout=10)
                app.session_state["_site_access_authenticated"] = True
                app.run()
        finally:
            if imported_persistence is not None:
                sys.modules["ticker_analyzer.persistence"] = imported_persistence

        self.assertFalse(app.exception)
        self.assertTrue(any("Restoring your saved" in element.value for element in app.caption))
        self.assertTrue(
            any("solely responsible" in element.value for element in app.sidebar.warning)
        )
        self.assertTrue(any(button.label == "Analyze" for button in app.sidebar.button))
        self.assertTrue(any("click Analyze now" in element.value for element in app.info))

    def test_switches_from_ranking_to_empty_analyzer_without_fetching(self):
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["page"] = "Large Cap Ranking"

        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(app.subheader[0].value, "Large Cap Ranking — Scoring v5.2")

        app.session_state["selected_tickers"] = []
        app.session_state["analysis_results"] = {}
        app.session_state["analysis_errors"] = {}
        app.sidebar.radio[0].set_value("Stock Analyzer").run()

        self.assertFalse(app.exception)
        self.assertTrue(any(element.value == "Add a ticker to start the analysis." for element in app.info))
        overwrite = next(
            button
            for button in app.sidebar.button
            if button.label == "Save / overwrite remembered setup"
        )
        overwrite.click().run()
        self.assertFalse(app.exception)
        self.assertTrue(
            any("Remembered setup overwritten" in element.value for element in app.sidebar.success)
        )

    def test_account_statement_page_renders_uploader_without_market_fetch(self):
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["page"] = "Account Statement"

        app.run()

        self.assertFalse(app.exception)
        self.assertEqual(app.subheader[0].value, "Account Statement")
        self.assertEqual(
            [uploader.label for uploader in app.file_uploader],
            ["Account statement", "Returns table (optional)"],
        )
        self.assertTrue(any("Choose an eToro" in element.value for element in app.info))

    def test_account_statement_survives_navigation_without_uploader_widget_value(self):
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["page"] = "Account Statement"
        app.session_state[ACCOUNT_STATEMENT_PAYLOAD_STATE_KEY] = statement_workbook()
        app.session_state[ACCOUNT_STATEMENT_NAME_STATE_KEY] = "remembered.xlsx"

        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(any("Loaded remembered.xlsx" in item.value for item in app.success))
        self.assertTrue(any(tab.label == "Analysis" for tab in app.tabs))

    def test_bulk_removes_checked_tickers_and_their_analysis_state(self):
        app = AppTest.from_string(
            textwrap.dedent(
                """
                import streamlit as st
                from ticker_analyzer.ui.sidebar import render_selected_tickers

                render_selected_tickers()
                """
            ),
            default_timeout=10,
        )
        app.session_state["selected_tickers"] = ["AAPL", "MSFT"]
        app.session_state["analysis_results"] = {"AAPL": {"ticker": "AAPL"}, "MSFT": {"ticker": "MSFT"}}
        app.session_state["analysis_errors"] = {"AAPL": "old", "MSFT": "old"}
        app.session_state["active_ticker"] = "AAPL"

        app.run()
        app.checkbox(key="select_remove_AAPL").check().run()
        app.checkbox(key="select_remove_MSFT").check().run()
        bulk_remove = next(button for button in app.button if button.label == "Remove selected (2)")
        bulk_remove.click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_tickers"], [])
        self.assertEqual(app.session_state["analysis_results"], {})
        self.assertEqual(app.session_state["analysis_errors"], {})
        self.assertIsNone(app.session_state["active_ticker"])

    def test_adds_exact_ticker_from_selected_international_market(self):
        with patch(
            "ticker_analyzer.ui.analysis_actions.analyze_selected_tickers",
            return_value=({}, {}),
        ) as analyze:
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["selected_tickers"] = []
            app.run()

            market = next(widget for widget in app.selectbox if widget.label == "Market")
            market.set_value("Poland (Warsaw)").run()
            symbol = next(widget for widget in app.text_input if widget.label == "Ticker symbol")
            symbol.set_value("PKN").run()
            add = next(button for button in app.button if button.label == "Add exact ticker")
            add.click().run()

            self.assertEqual(analyze.call_count, 1)
            app.session_state["analysis_pending_since"] = 0.0
            app.run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_tickers"], ["PKN.WA"])
        self.assertEqual(app.session_state["active_ticker"], "PKN.WA")
        self.assertEqual(analyze.call_count, 2)
        self.assertFalse(app.session_state["analysis_pending_changes"])

    def test_stock_analyzer_exposes_simulation_tab_for_analyzed_tickers(self):
        result = {
            "ticker": "AAPL",
            "company_name": "Apple",
            "currency": "USD",
            "profile": "Industrial",
            "current_price": 100,
            "overall_score": 80,
            "rating": "Buy",
            "tabs": {
                name: {"score": 80, "rating": "Buy", "metrics": [], "coverage": {"percentage": 100}}
                for name in ("Growth", "Fundamentals", "Value")
            },
            "charts": {},
            "missing": [],
        }
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["selected_tickers"] = ["AAPL"]
        app.session_state["analysis_results"] = {"AAPL": result}
        app.session_state["analysis_errors"] = {}
        app.session_state["active_ticker"] = "AAPL"

        # A stale lazy facade may survive a Streamlit Cloud hot reload. The app
        # must load the simulation view directly instead of relying on that map.
        with patch(
            "ticker_analyzer.ui.views.render_simulation",
            side_effect=AssertionError("stale facade used"),
            create=True,
        ):
            app.run()

        self.assertFalse(app.exception)
        self.assertTrue(any(tab.label == "Simulation" for tab in app.tabs))
        self.assertTrue(any(header.value == "Portfolio Simulation" for header in app.subheader))

        def market_data(_results, start_date, end_date, _currency, **_kwargs):
            prices = pd.Series([100.0, 110.0], index=pd.to_datetime([start_date, end_date]))
            return {"AAPL": prices}, {"AAPL": pd.Series(dtype=float)}, []

        with patch(
            "ticker_analyzer.ui.simulation_view._fetch_simulation_market_data",
            side_effect=market_data,
        ):
            next(button for button in app.button if button.label == "Run simulation").click().run()

        self.assertFalse(app.exception)
        self.assertAlmostEqual(
            app.session_state["simulation_output"]["result"].buy_and_hold.final_value,
            11_000,
        )

    def test_account_statement_pseudo_ticker_can_open_simulation_without_stocks(self):
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["selected_tickers"] = [ACCOUNT_STATEMENT_TICKER]
        app.session_state["analysis_results"] = {}
        app.session_state["analysis_errors"] = {}
        app.session_state[ACCOUNT_RETURNS_STATE_KEY] = ReturnsTable({(2024, 1): 0.10})

        app.run()

        self.assertFalse(app.exception)
        self.assertTrue(any(tab.label == "Simulation" for tab in app.tabs))
        self.assertTrue(any("ACC_STMT represents" in item.value for item in app.info))

    def test_imported_account_statement_ticker_can_be_selected_from_sidebar(self):
        app = AppTest.from_file("app.py", default_timeout=10)
        app.session_state["_site_access_authenticated"] = True
        app.session_state["selected_tickers"] = []
        app.session_state["analysis_results"] = {}
        app.session_state["analysis_errors"] = {}
        app.session_state[ACCOUNT_RETURNS_STATE_KEY] = ReturnsTable({(2024, 1): 0.10})

        app.run()
        next(button for button in app.sidebar.button if button.label == "Add ACC_STMT").click().run()

        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_tickers"], [ACCOUNT_STATEMENT_TICKER])

    def test_ranking_country_filter_limits_displayed_rows(self):
        app = AppTest.from_string(
            textwrap.dedent(
                """
                from ticker_analyzer.ui import ranking_view

                ranking_view.load_ranking = lambda: {
                    "metadata": {
                        "complete": True,
                        "requested": 2,
                        "analyzed": 2,
                        "scored": 2,
                        "insufficient_data": 0,
                    },
                    "companies": [
                        {
                            "rank": 1,
                            "ticker": "PKN.WA",
                            "company_name": "Orlen",
                            "country": "Poland",
                            "exchange": "WSE",
                            "profile": "Industrial",
                            "overall_score": 80.0,
                            "rating": "Strong Buy",
                            "data_quality": 75.0,
                        },
                        {
                            "rank": 2,
                            "ticker": "AAPL",
                            "company_name": "Apple",
                            "country": "United States",
                            "exchange": "NASDAQ",
                            "profile": "Industrial",
                            "overall_score": 70.0,
                            "rating": "Buy",
                            "data_quality": 80.0,
                        },
                    ],
                    "errors": [],
                }
                ranking_view.render_large_cap_ranking()
                """
            ),
            default_timeout=10,
        )

        app.run()
        country_filter = next(widget for widget in app.multiselect if widget.label == "Country")
        country_filter.set_value(["Poland"]).run()

        self.assertFalse(app.exception)
        table = app.dataframe[0].value
        self.assertEqual(list(table["Ticker"]), ["PKN.WA"])


if __name__ == "__main__":
    unittest.main()
