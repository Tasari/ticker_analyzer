import unittest
from unittest.mock import patch

from ticker_analyzer.ui.state import add_tickers_to_state, initialize_state, remove_tickers_from_state


class UiStateTest(unittest.TestCase):
    def test_initialize_state_includes_persisted_range_defaults(self):
        state = {}
        with patch("ticker_analyzer.ui.state.st.session_state", state):
            initialize_state()

        self.assertEqual(state["selected_tickers"], ["AFRM"])
        self.assertEqual(state["growth_range"], "2Y")
        self.assertEqual(state["fundamentals_range"], "2Y")
        self.assertEqual(state["value_range"], "2Y")
        self.assertFalse(state["analysis_pending_changes"])
        self.assertFalse(state["automatic_analysis_attempted"])
        self.assertFalse(state["automatic_analysis_requested"])
        self.assertIsNone(state["analysis_pending_since"])

    def test_removes_multiple_tickers_and_related_session_data(self):
        state = {
            "selected_tickers": ["A", "B", "C"],
            "analysis_results": {"A": {}, "B": {}, "C": {}},
            "analysis_errors": {"B": "error", "C": "error"},
            "active_ticker": "B",
            "select_remove_A": True,
            "select_remove_B": True,
        }

        remove_tickers_from_state(state, ["A", "B"])

        self.assertEqual(state["selected_tickers"], ["C"])
        self.assertEqual(state["analysis_results"], {"C": {}})
        self.assertEqual(state["analysis_errors"], {"C": "error"})
        self.assertEqual(state["active_ticker"], "C")
        self.assertNotIn("select_remove_A", state)
        self.assertNotIn("select_remove_B", state)

    def test_removing_last_active_ticker_clears_active_selection(self):
        state = {
            "selected_tickers": ["A"],
            "analysis_results": {"A": {}},
            "analysis_errors": {},
            "active_ticker": "A",
        }

        remove_tickers_from_state(state, ["A"])

        self.assertEqual(state["selected_tickers"], [])
        self.assertEqual(state["analysis_results"], {})
        self.assertIsNone(state["active_ticker"])

    def test_adds_normalized_tickers_from_different_markets(self):
        state = {
            "selected_tickers": ["AAPL"],
            "analysis_results": {"AAPL": {}},
            "analysis_errors": {"OLD": "error"},
            "active_ticker": "AAPL",
        }

        added = add_tickers_to_state(state, ["pkn.wa", "9988.hk", "AAPL", "bad ticker"])

        self.assertEqual(added, ["PKN.WA", "9988.HK"])
        self.assertEqual(state["selected_tickers"], ["AAPL", "PKN.WA", "9988.HK"])
        self.assertEqual(state["analysis_results"], {"AAPL": {}})
        self.assertEqual(state["analysis_errors"], {"OLD": "error"})
        self.assertEqual(state["active_ticker"], "AAPL")
        self.assertTrue(state["analysis_pending_changes"])
        self.assertFalse(state["automatic_analysis_requested"])
        self.assertIsInstance(state["analysis_pending_since"], float)

    def test_account_statement_pseudo_ticker_is_a_valid_selection(self):
        state = {"selected_tickers": [], "analysis_results": {}, "analysis_errors": {}}

        added = add_tickers_to_state(state, ["acc_stmt"])

        self.assertEqual(added, ["ACC_STMT"])
        self.assertEqual(state["selected_tickers"], ["ACC_STMT"])
        self.assertEqual(state["active_ticker"], "ACC_STMT")


if __name__ == "__main__":
    unittest.main()
