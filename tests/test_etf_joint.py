from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest
from tests.test_etf import fund_result
from ticker_analyzer.providers.etf_joint import combine_etf_holdings


def result(ticker, positions):
    return replace(
        fund_result(),
        ticker=ticker,
        holdings=pd.DataFrame(
            positions,
            columns=["Ticker", "Company", "Weight (%)"],
        ),
    )


FUNDS = {
    "A": result("A", [("NVDA", "NVIDIA", 10), ("AAPL", "Apple", 5)]),
    "B": result("B", [("NVDA", "NVIDIA", 20), ("TSM", "TSMC", 15)]),
    "C": result("C", [("NVDA", "NVIDIA", 30)]),
}


class JointHoldingsTest(unittest.TestCase):
    def test_tsmc_local_and_adr_listings_merge_by_name_without_share_conversion(self):
        funds = {
            "A": result("A", [("TPE: 2330", "Taiwan Semiconductor Manufacturing Company Limited", 10)]),
            "B": result("B", [("TSM", "Taiwan Semiconductor Manufacturing Co., Ltd. Sponsored ADR", 20)]),
            "C": result("C", [("2330.TW", "TSMC", 30)]),
        }
        joint = combine_etf_holdings(funds, {"A": 50, "B": 30, "C": 20})
        self.assertEqual(len(joint.holdings), 1)
        position = joint.holdings.iloc[0]
        self.assertEqual(position["Ticker"], "TSM")
        self.assertEqual(position["Listings"], "TPE: 2330, TSM, 2330.TW")
        self.assertEqual(position["ETF count"], 3)
        self.assertEqual(position["Joint weight (%)"], 17)
        self.assertEqual(joint.fund_coverage, {"A": 5, "B": 6, "C": 6})

    def test_distinct_listings_in_one_fund_contribute_but_listing_aliases_do_not_duplicate(self):
        funds = {
            "A": result("A", [("TPE: 2330", "TSMC", 4), ("2330.TW", "TSMC", 4), ("TSM", "TSMC", 6)]),
            "B": result("B", [("TSM", "TSMC", 20)]),
        }
        joint = combine_etf_holdings(funds, {"A": 50, "B": 50})
        self.assertEqual(len(joint.holdings), 1)
        self.assertEqual(joint.holdings.iloc[0]["Joint weight (%)"], 15)
        self.assertEqual(joint.holdings.iloc[0]["ETF count"], 2)
        self.assertEqual(joint.fund_coverage, {"A": 5, "B": 10})

    def test_name_variants_and_share_classes_merge_without_fuzzy_matches(self):
        a = result(
            "A",
            [
                ("NESN.SW", "Nestlé S.A.", 10),
                ("GOOGL", "Alphabet Inc. Class A", 5),
                ("TSM", "TSMC", 3),
                ("2383.TW", "Taiwan Semiconductor Co., Ltd.", 2),
                ("PARENT", "Example Corporation", 2),
                ("HOLDING", "Example Holdings Ltd.", 2),
            ],
        )
        b = result("B", [("NSRGY", "NESTLE SA", 20), ("GOOG", "Alphabet Class C", 7)])
        joint = combine_etf_holdings({"A": a, "B": b}, {"A": 50, "B": 50})
        self.assertEqual(len(joint.holdings), 6)
        positions = joint.holdings.set_index("Ticker")
        self.assertEqual(positions.loc["NSRGY", "Joint weight (%)"], 15)
        self.assertEqual(positions.loc["GOOGL", "Joint weight (%)"], 6)
        self.assertEqual(positions.loc["TSM", "Joint weight (%)"], 1.5)
        self.assertEqual(positions.loc["2383.TW", "Joint weight (%)"], 1)
        self.assertEqual(positions.loc["PARENT", "ETF count"], 1)
        self.assertEqual(positions.loc["HOLDING", "ETF count"], 1)

    def test_known_company_with_unresolved_symbol_retains_exposure_and_local_only_tsmc_stays_local(self):
        a = result("A", [("TPE: 2330", "TSMC", 10), ("UNSUPPORTED: ABC", "Example Ltd.", 5)])
        joint = combine_etf_holdings({"A": a}, {"A": 100})
        self.assertEqual(joint.holdings["Ticker"].tolist(), ["2330.TW", "—"])
        self.assertEqual(joint.holdings["Listings"].tolist(), ["TPE: 2330", "UNSUPPORTED: ABC"])
        self.assertEqual(joint.fund_coverage, {"A": 15})

    def test_unavailable_company_names_fall_back_to_ticker_identity(self):
        a = result("A", [("AAPL", "Unknown", 10), ("NVDA", "Unknown", 5)])
        b = result("B", [("AAPL", "N/A", 20)])
        joint = combine_etf_holdings({"A": a, "B": b}, {"A": 50, "B": 50})
        self.assertEqual(joint.holdings["Ticker"].tolist(), ["AAPL", "NVDA"])
        self.assertEqual(joint.holdings.iloc[0]["Joint weight (%)"], 15)
        self.assertEqual(joint.holdings.iloc[0]["ETF count"], 2)

    def test_three_funds_combine_nvidia_once_with_weighted_exposure_and_partial_coverage(self):
        joint = combine_etf_holdings(FUNDS, {ticker: 100 / 3 for ticker in FUNDS})
        nvda = joint.holdings.set_index("Ticker").loc["NVDA"]
        self.assertAlmostEqual(nvda["Joint weight (%)"], 20)
        self.assertEqual(nvda["ETF count"], 3)
        self.assertEqual(nvda["ETFs"], "A, B, C")
        self.assertEqual(joint.holdings["Ticker"].tolist(), ["NVDA", "TSM", "AAPL"])
        self.assertAlmostEqual(joint.holdings["Joint weight (%)"].sum(), 26.6666667)
        self.assertAlmostEqual(sum(joint.fund_coverage.values()), 26.6666667)

    def test_custom_allocations_missing_funds_and_zero_weight_funds_are_not_renormalized(self):
        allocations = {"A": 50, "B": 30, "C": 20}
        self.assertAlmostEqual(combine_etf_holdings(FUNDS, allocations).holdings.iloc[0]["Joint weight (%)"], 17)
        joint = combine_etf_holdings({"A": FUNDS["A"], "B": FUNDS["B"]}, allocations)
        self.assertAlmostEqual(joint.holdings.iloc[0]["Joint weight (%)"], 11)
        self.assertEqual(joint.missing_funds, ["C"])
        self.assertEqual(joint.holdings.iloc[0]["ETF count"], 2)
        joint = combine_etf_holdings(FUNDS, {"A": 100, "B": 0, "MISSING": 0})
        self.assertEqual(joint.holdings.iloc[0]["ETF count"], 1)
        self.assertEqual(joint.missing_funds, [])

    def test_aliases_merge_without_double_counting_a_fund_and_invalid_symbols_are_excluded(self):
        a = result("A", [("BRK.B", "Berkshire", 4), ("BRK-B", "Berkshire", 4), ("—", "Unknown", 20)])
        a.source = "Stock Analysis / Finnhub"
        b = result("B", [("BRK-B", "Berkshire", 6)])
        joint = combine_etf_holdings({"A": a, "B": b}, {"A": 50, "B": 50})
        self.assertEqual(len(joint.holdings), 1)
        self.assertEqual(joint.holdings.iloc[0]["Ticker"], "BRK-B")
        self.assertEqual(joint.holdings.iloc[0]["Joint weight (%)"], 5)
        self.assertEqual(joint.holdings.iloc[0]["ETF count"], 2)

    def test_invalid_allocations_and_mismatched_fund_identity(self):
        for allocations in ({}, {"A": 0}, {"A": -5, "B": 105}, {"A": float("nan")}, {"A": float("inf")}):
            with self.subTest(allocations=allocations), self.assertRaises(ValueError):
                combine_etf_holdings(FUNDS, allocations)
        joint = combine_etf_holdings({"WRONG": FUNDS["A"]}, {"WRONG": 100})
        self.assertTrue(joint.holdings.empty)
        self.assertEqual(joint.missing_funds, ["WRONG"])


class JointViewTest(unittest.TestCase):
    def test_merged_company_can_be_added_once_without_triggering_analysis(self):
        funds = {
            "A": result("A", [("TPE: 2330", "Taiwan Semiconductor Manufacturing Company Limited", 10)]),
            "B": result("B", [("TSM", "TSMC", 20)]),
        }
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_etfs"] = list(funds)
            app.session_state["selected_tickers"] = []
            app.session_state["etf_holdings_by_ticker"] = funds
            app.run()
            self.assertFalse(app.exception)
            table = next(table for table in app.dataframe if str(table.key).startswith("etf_joint_table_"))
            self.assertEqual(table.value["Ticker"].tolist(), ["TSM"])
            self.assertEqual(table.value.iloc[0]["Listings"], "TPE: 2330, TSM")
            self.assertEqual(table.value.iloc[0]["Joint weight (%)"], 15)
            app.session_state[table.key] = {"selection": {"rows": [0], "columns": [], "cells": []}}
            app.run()
            with patch("ticker_analyzer.ui.analysis_actions.analyze_selected_tickers") as analyze:
                app.button(key="etf_joint_add_companies").click().run()
                analyze.assert_not_called()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_tickers"], ["TSM"])

    def test_unresolved_listing_is_visible_but_cannot_be_added_to_analysis(self):
        app = AppTest.from_string(
            "import streamlit as st\nfrom ticker_analyzer.ui.etf_joint_view import render_joint\n"
            "render_joint(st.session_state['funds'], st.session_state['results'])"
        )
        app.session_state["funds"] = ["A"]
        app.session_state["results"] = {"A": result("A", [("UNSUPPORTED: ABC", "Example Ltd.", 10)])}
        app.run()
        self.assertFalse(app.exception)
        table = next(table for table in app.dataframe if str(table.key).startswith("etf_joint_table_"))
        app.session_state[table.key] = {"selection": {"rows": [0], "columns": [], "cells": []}}
        app.run()
        self.assertTrue(app.button(key="etf_joint_add_companies").disabled)
        self.assertEqual(
            next(metric.value for metric in app.metric if metric.label == "Known share of the set"), "10.00%"
        )

    def test_equal_default_custom_weights_shared_filter_and_selection_survive_navigation(self):
        with patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "ETF"
            app.session_state["selected_etfs"] = list(FUNDS)
            app.session_state["selected_tickers"] = ["AAPL"]
            app.session_state["etf_holdings_by_ticker"] = FUNDS
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(app.checkbox(key="etf_joint_equal_weights").value)
            app.checkbox(key="etf_joint_equal_weights").uncheck().run()
            app.number_input(key="etf_joint_allocation_A").set_value(50)
            app.number_input(key="etf_joint_allocation_B").set_value(30)
            app.number_input(key="etf_joint_allocation_C").set_value(20).run()
            self.assertFalse(app.exception)
            joint_table = next(table for table in app.dataframe if str(table.key).startswith("etf_joint_table_"))
            self.assertAlmostEqual(joint_table.value.iloc[0]["Joint weight (%)"], 17)
            app.checkbox(key="etf_joint_shared_only").check().run()
            joint_table = next(table for table in app.dataframe if str(table.key).startswith("etf_joint_table_"))
            self.assertEqual(joint_table.value["Ticker"].tolist(), ["NVDA"])
            app.sidebar.radio[0].set_value("Simulation").run()
            app.sidebar.radio[0].set_value("ETF").run()
            self.assertFalse(app.checkbox(key="etf_joint_equal_weights").value)
            self.assertEqual(app.number_input(key="etf_joint_allocation_A").value, 50)
            joint_table = next(table for table in app.dataframe if str(table.key).startswith("etf_joint_table_"))
            app.session_state[joint_table.key] = {"selection": {"rows": [0], "columns": [], "cells": []}}
            app.run()
            with patch("ticker_analyzer.ui.analysis_actions.analyze_selected_tickers", return_value=({}, {})):
                next(button for button in app.button if button.label == "Add joint companies to Analyzer").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["selected_tickers"], ["AAPL", "NVDA"])
            self.assertEqual(app.session_state["page"], "Stock Analyzer")

    def test_invalid_total_hides_joint_result_until_weights_total_one_hundred(self):
        app = AppTest.from_string(
            "import streamlit as st\nfrom ticker_analyzer.ui.etf_joint_view import render_joint\n"
            "render_joint(st.session_state['funds'], st.session_state['results'])"
        )
        app.session_state["funds"] = list(FUNDS)
        app.session_state["results"] = FUNDS
        app.session_state["etf_joint_equal_weights"] = False
        app.session_state["etf_joint_allocation_A"] = 90.0
        app.session_state["etf_joint_allocation_B"] = 90.0
        app.session_state["etf_joint_allocation_C"] = 90.0
        app.run()
        self.assertFalse(app.exception)
        self.assertFalse(app.dataframe)
        self.assertTrue(any("270.00%" in info.value for info in app.info))
