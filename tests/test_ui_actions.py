import threading
import time
import unittest
from unittest.mock import patch

from ticker_analyzer.ui.actions import analysis_worker_count, analyze_selected_tickers


class UiActionsTest(unittest.TestCase):
    def test_analysis_worker_count_is_capped(self):
        self.assertEqual(analysis_worker_count([]), 1)
        self.assertEqual(analysis_worker_count(["ONE", "TWO"]), 2)
        self.assertEqual(analysis_worker_count(["A", "B", "C", "D", "E", "F"]), 5)

    def test_single_ticker_analysis_uses_sequential_path(self):
        with (
            patch("ticker_analyzer.ui.actions.analyze_tickers_sequentially", return_value=({"ONE": {}}, {})) as sequential,
            patch("ticker_analyzer.ui.actions.ThreadPoolExecutor") as executor,
        ):
            results, errors = analyze_selected_tickers(["ONE"], {"Growth": "2Y"}, {})

        sequential.assert_called_once()
        executor.assert_not_called()
        self.assertEqual(results, {"ONE": {}})
        self.assertEqual(errors, {})

    def test_parallel_analysis_preserves_selected_ticker_order(self):
        def fake_analyze(ticker: str, ranges: dict[str, str], config: dict) -> dict:
            if ticker == "SLOW":
                time.sleep(0.05)
            return {"ticker": ticker}

        with patch("ticker_analyzer.ui.actions.analyze_ticker", side_effect=fake_analyze):
            results, errors = analyze_selected_tickers(["SLOW", "FAST"], {"Growth": "2Y"}, {})

        self.assertEqual(list(results), ["SLOW", "FAST"])
        self.assertEqual(errors, {})

    def test_parallel_analysis_handles_mixed_success_and_errors(self):
        def fake_analyze(ticker: str, ranges: dict[str, str], config: dict) -> dict:
            if ticker == "BAD":
                raise ValueError("No data returned for BAD.")
            if ticker == "BROKEN":
                raise RuntimeError("boom")
            return {"ticker": ticker}

        with (
            patch("ticker_analyzer.ui.actions.analyze_ticker", side_effect=fake_analyze),
            patch("ticker_analyzer.ui.actions.logger"),
        ):
            results, errors = analyze_selected_tickers(["GOOD", "BAD", "BROKEN"], {"Growth": "2Y"}, {})

        self.assertEqual(list(results), ["GOOD"])
        self.assertEqual(errors["BAD"], "No data returned for BAD.")
        self.assertEqual(errors["BROKEN"], "Unexpected internal error. Check application logs.")

    def test_parallel_analysis_runs_work_concurrently(self):
        barrier = threading.Barrier(2)

        def fake_analyze(ticker: str, ranges: dict[str, str], config: dict) -> dict:
            barrier.wait(timeout=1)
            return {"ticker": ticker}

        with patch("ticker_analyzer.ui.actions.analyze_ticker", side_effect=fake_analyze):
            results, errors = analyze_selected_tickers(["ONE", "TWO"], {"Growth": "2Y"}, {})

        self.assertEqual(list(results), ["ONE", "TWO"])
        self.assertEqual(errors, {})


if __name__ == "__main__":
    unittest.main()
