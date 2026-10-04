from __future__ import annotations

import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class RankingUpdateViewTest(unittest.TestCase):
    def setUp(self):
        patches = {
            "permission": patch("ticker_analyzer.ui.ranking_view.mutation_allowed", return_value=True),
            "running": patch("ticker_analyzer.ui.ranking_view.ranking_refresh_is_running", return_value=False),
            "snapshots": patch("ticker_analyzer.ui.ranking_view.available_ranking_snapshots", return_value=0),
            "stocks": patch(
                "ticker_analyzer.ui.ranking_view.refresh_large_cap_ranking",
                return_value=(True, "stock snapshot saved", {}),
            ),
            "etfs": patch(
                "ticker_analyzer.ranking.assets.refresh_etf_ranking",
                return_value={"companies": [{"ticker": "VVSM.DE"}]},
            ),
            "crypto": patch(
                "ticker_analyzer.ranking.assets.refresh_crypto_ranking",
                return_value={"companies": [{"ticker": "BTC-USD"}]},
            ),
        }
        self.mocks = {}
        for name, item in patches.items():
            self.mocks[name] = item.start()
            self.addCleanup(item.stop)
        self.app = AppTest.from_string(
            "from ticker_analyzer.ui.ranking_view import _render_all_ranking_controls\n_render_all_ranking_controls()",
            default_timeout=10,
        ).run()

    def click(self, label):
        next(button for button in self.app.button if button.label == label).click().run()
        self.assertFalse(self.app.exception)

    def test_all_refreshes_assets_before_stock_build_and_retains_success_after_rerun(self):
        def stock_build(**kwargs):
            self.mocks["etfs"].assert_called_once()
            self.mocks["crypto"].assert_called_once()
            kwargs["progress_callback"]({"processed": 25, "requested": 100})
            return True, "stock snapshot saved", {}

        self.mocks["stocks"].side_effect = stock_build
        self.click("Update all rankings")
        self.mocks["stocks"].assert_called_once()
        self.assertTrue(
            any(all(label in item.value for label in ("Stocks", "ETFs", "Crypto")) for item in self.app.success)
        )
        self.app.run()
        for name in ("stocks", "etfs", "crypto"):
            self.mocks[name].assert_called_once()
        self.assertEqual([item.label for item in self.app.expander], ["Stocks", "ETFs", "Crypto"])

    def test_individual_dropdowns_refresh_only_selected_snapshot(self):
        for selected, label in (("stocks", "Stocks"), ("etfs", "ETFs"), ("crypto", "Crypto")):
            with self.subTest(selected=selected):
                for name in ("stocks", "etfs", "crypto"):
                    self.mocks[name].reset_mock()
                self.click(f"Update {label} only")
                self.mocks[selected].assert_called_once()
                for other in {"stocks", "etfs", "crypto"} - {selected}:
                    self.mocks[other].assert_not_called()
                outcomes = self.app.session_state["ranking_update_outcomes"]
                self.assertEqual([item[0] for item in outcomes], [label])

    def test_asset_failure_does_not_prevent_other_rankings_and_is_visible(self):
        self.mocks["etfs"].side_effect = RuntimeError("provider unavailable; previous snapshot retained")
        self.click("Update all rankings")
        self.mocks["stocks"].assert_called_once()
        self.mocks["crypto"].assert_called_once()
        self.assertTrue(
            any("ETFs" in item.value and "previous snapshot retained" in item.value for item in self.app.error)
        )
        self.assertTrue(any("Stocks" in item.value and "Crypto" in item.value for item in self.app.success))

    def test_stock_exception_does_not_skip_asset_snapshots_or_lose_status(self):
        self.mocks["stocks"].side_effect = OSError("generator unavailable")
        self.click("Update all rankings")
        self.mocks["etfs"].assert_called_once()
        self.mocks["crypto"].assert_called_once()
        self.assertTrue(
            any("Stocks" in item.value and "generator unavailable" in item.value for item in self.app.error)
        )
        self.assertTrue(any("ETFs" in item.value and "Crypto" in item.value for item in self.app.success))

    def test_single_stock_restart_keeps_other_rankings_unchanged(self):
        self.mocks["running"].return_value = True
        with patch("ticker_analyzer.ui.ranking_view._render_running_stock_progress"):
            self.click("Update Stocks only")
            for name in ("stocks", "etfs", "crypto"):
                self.mocks[name].assert_not_called()
            self.assertTrue(any("begin Stocks again" in item.value for item in self.app.warning))
            self.click("Yes, restart")
        self.mocks["stocks"].assert_called_once()
        self.assertTrue(self.mocks["stocks"].call_args.kwargs["restart_running"])
        self.mocks["etfs"].assert_not_called()
        self.mocks["crypto"].assert_not_called()

    def test_market_update_does_not_restart_running_stocks(self):
        self.mocks["running"].return_value = True
        with patch("ticker_analyzer.ui.ranking_view._render_running_stock_progress"):
            self.click("Update Crypto only")
        self.mocks["stocks"].assert_not_called()
        self.mocks["etfs"].assert_not_called()
        self.mocks["crypto"].assert_called_once()
        self.assertFalse(any(button.label == "Yes, restart" for button in self.app.button))

    def test_readonly_mode_disables_all_refresh_buttons(self):
        self.mocks["permission"].return_value = False
        self.app.run()
        updates = [button for button in self.app.button if button.label.startswith("Update")]
        self.assertEqual(len(updates), 4)
        self.assertTrue(all(button.disabled for button in updates))

    def test_app_route_ignores_cached_ranking_facade_from_old_deployment(self):
        with (
            patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1"}),
            patch(
                "ticker_analyzer.ui.views.render_large_cap_ranking", side_effect=AssertionError("old ranking page used")
            ),
            patch("ticker_analyzer.ui.ranking_view._render_stock_ranking"),
            patch("ticker_analyzer.ui.ranking_view.render_etf_ranking"),
            patch("ticker_analyzer.ui.ranking_view.render_crypto_ranking"),
        ):
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "Large Cap Ranking"
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual([item.label for item in app.expander], ["Stocks", "ETFs", "Crypto"])


if __name__ == "__main__":
    unittest.main()
