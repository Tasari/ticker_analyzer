from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import ticker_analyzer


class PublicApiTest(unittest.TestCase):
    def test_public_facade_delegates_analysis_and_formatting(self):
        with patch("ticker_analyzer.analysis.engine.analyze_ticker", return_value={"ticker": "AAA"}) as analyze:
            self.assertEqual(ticker_analyzer.analyze_ticker("AAA", "1y", {"version": 5}), {"ticker": "AAA"})
            analyze.assert_called_once_with("AAA", "1y", {"version": 5})
        with patch("ticker_analyzer.scoring.format_metric_value", return_value="12.0%") as formatter:
            self.assertEqual(ticker_analyzer.format_metric_value(12, "%"), "12.0%")
            formatter.assert_called_once_with(12, "%")

    def test_public_facade_delegates_config_with_default_and_custom_paths(self):
        custom = Path("custom.json")
        with patch("ticker_analyzer.config.load_config", return_value={"version": 5}) as load:
            self.assertEqual(ticker_analyzer.load_config(custom), {"version": 5})
            load.assert_called_once_with(custom)
        with patch("ticker_analyzer.config.save_config") as save:
            ticker_analyzer.save_config({"version": 5}, custom)
            save.assert_called_once_with({"version": 5}, custom)

        with (
            patch("ticker_analyzer.config.CONFIG_PATH", Path("default.json")),
            patch("ticker_analyzer.config.load_config", return_value={}) as load,
            patch("ticker_analyzer.config.save_config") as save,
        ):
            ticker_analyzer.load_config()
            ticker_analyzer.save_config({})
            load.assert_called_once_with(Path("default.json"))
            save.assert_called_once_with({}, Path("default.json"))


if __name__ == "__main__":
    unittest.main()
