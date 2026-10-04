import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class SidebarTest(unittest.TestCase):
    def test_restored_and_new_selections_wait_for_manual_analysis(self):
        with (
            patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}),
            patch("ticker_analyzer.ui.analysis_actions.analyze_selected_tickers", return_value=({}, {})) as analyze,
        ):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["selected_tickers"] = ["NVDA"]
            app.run()
            analyze.assert_not_called()
            self.assertFalse(app.exception)
            self.assertTrue(any("Click Analyze" in item.value for item in app.info))

            app.session_state["analysis_pending_changes"] = True
            app.session_state["automatic_analysis_requested"] = True
            app.session_state["analysis_pending_since"] = 0.0
            app.run()
            analyze.assert_not_called()
            self.assertTrue(any("Click Analyze" in item.value for item in app.sidebar.caption))
            self.assertFalse(any("automatically" in item.value for item in app.sidebar.caption))

            next(button for button in app.sidebar.button if button.label == "Analyze").click().run()
            analyze.assert_called_once()
            self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()
