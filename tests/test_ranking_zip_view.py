from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tests.test_ranking_bundle import archive_bytes, snapshot
from ticker_analyzer.ranking.bundle import RANKING_SNAPSHOTS, import_rankings_archive
from ticker_analyzer.ranking.storage import load_ranking


class RankingZipViewTest(unittest.TestCase):
    def test_import_refreshes_all_three_ranking_tabs_and_shows_success_after_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory) / name for name in RANKING_SNAPSHOTS}
            entries = [
                ("stocks_ranking.json", snapshot("AAPL")),
                ("etfs_ranking.json", snapshot("VVSM.DE", "ETF")),
                ("crypto_ranking.json", snapshot("BTC-USD", "Crypto")),
            ]
            uploaded = BytesIO(archive_bytes(entries))
            app = AppTest.from_file("app.py", default_timeout=10)
            app.session_state["_site_access_authenticated"] = True
            app.session_state["page"] = "Large Cap Ranking"

            def read_snapshot(path=RANKING_SNAPSHOTS["stocks_ranking.json"]):
                name = next((name for name, target in RANKING_SNAPSHOTS.items() if target == path), None)
                return load_ranking(paths[name]) if name else {"metadata": {}, "companies": [], "errors": []}

            with (
                patch.dict("os.environ", {"TICKER_ANALYZER_DISABLE_BROWSER_STORAGE": "1", "APP_MODE": "local"}),
                patch("ticker_analyzer.ui.ranking_view.ranking_refresh_is_running", return_value=False),
                patch("ticker_analyzer.ui.ranking_view.st.file_uploader", return_value=uploaded),
                patch("ticker_analyzer.ui.ranking_view.load_ranking", side_effect=read_snapshot),
                patch("ticker_analyzer.ui.market_ranking_view.load_ranking", side_effect=read_snapshot),
                patch("ticker_analyzer.ui.ranking_view.import_rankings_archive", side_effect=lambda data: import_rankings_archive(data, paths)) as importer,
            ):
                app.run()
                next(button for button in app.button if button.label == "Import ZIP").click().run()
            self.assertFalse(app.exception)
            importer.assert_called_once_with(uploaded.getvalue())
            self.assertEqual([tab.label for tab in app.tabs], ["Stocks", "ETFs", "Crypto"])
            self.assertTrue(any("Stocks — 1 rows; ETFs — 1 rows; Crypto — 1 rows" in item.value for item in app.success))
            for ticker in ("AAPL", "VVSM.DE", "BTC-USD"):
                self.assertTrue(any("Ticker" in frame.value and ticker in frame.value["Ticker"].tolist() for frame in app.dataframe))
            self.assertFalse(any(item.label == "Import stock ranking snapshot" for item in app.expander))

    def test_invalid_zip_shows_error_without_switching_or_success(self):
        app = AppTest.from_string(
            "from ticker_analyzer.ui.ranking_view import _render_archive_import\n_render_archive_import(refresh_running=False)"
        )
        with patch("ticker_analyzer.ui.ranking_view.st.file_uploader", return_value=BytesIO(b"broken zip")), patch.dict("os.environ", {"APP_MODE": "local"}):
            app.run()
            next(button for button in app.button if button.label == "Import ZIP").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Ranking import failed" in item.value for item in app.error))
        self.assertFalse(app.success)

    def test_import_is_disabled_during_refresh_or_when_production_permission_is_off(self):
        for running, app_mode in ((True, "local"), (False, "production")):
            app = AppTest.from_string(
                "from ticker_analyzer.ui.ranking_view import _render_archive_import\n"
                f"_render_archive_import(refresh_running={running})"
            )
            with patch.dict("os.environ", {"APP_MODE": app_mode, "ALLOW_RANKING_IMPORT": ""}), patch("ticker_analyzer.ui.ranking_view.st.file_uploader", return_value=BytesIO(b"zip")):
                app.run()
            self.assertFalse(app.exception)
            self.assertTrue(next(button for button in app.button if button.label == "Import ZIP").disabled)
