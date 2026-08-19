import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ticker_analyzer.ranking import save_ranking
from ticker_analyzer.ui.actions import (
    analysis_worker_count,
    analyze_selected_tickers,
    cached_ticker_analysis,
    ranking_refresh_is_complete,
    refresh_large_cap_ranking,
)


class UiActionsTest(unittest.TestCase):
    def test_complete_refresh_accepts_processed_ticker_errors(self):
        payload = {
            "metadata": {"complete": True, "requested": 3},
            "companies": [{"ticker": "A"}, {"ticker": "B"}],
            "errors": [{"ticker": "C", "error": "unavailable"}],
        }
        self.assertTrue(ranking_refresh_is_complete(payload, expected_limit=3))

    def test_refresh_rejects_incomplete_processing(self):
        payload = {
            "metadata": {"complete": True, "requested": 3},
            "companies": [{"ticker": "A"}],
            "errors": [{"ticker": "B", "error": "unavailable"}],
        }
        self.assertFalse(ranking_refresh_is_complete(payload, expected_limit=3))

    def test_refresh_ranking_replaces_snapshot_only_after_complete_result(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ranking.json"
            save_ranking({"metadata": {"complete": False}, "companies": [{"ticker": "OLD"}], "errors": []}, output)
            refresh = output.with_suffix(".refresh.json")
            payload = {
                "metadata": {"complete": True, "scored": 1, "insufficient_data": 0},
                "companies": [{"ticker": "NEW"}],
                "errors": [],
            }
            save_ranking(payload, refresh)
            with patch(
                "ticker_analyzer.ui.actions.subprocess.run",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ) as run:
                success, message, _ = refresh_large_cap_ranking(output, limit=1)

            self.assertTrue(success)
            command = run.call_args.args[0]
            self.assertEqual(command[1:3], ["-m", "scripts.build_large_cap_ranking"])
            self.assertEqual(run.call_args.kwargs["cwd"], Path(__file__).resolve().parents[1])
            self.assertIn("Ranking updated", message)
            self.assertFalse(refresh.exists())
            self.assertIn('"NEW"', output.read_text(encoding="utf-8"))

    def test_refresh_ranking_rejects_concurrent_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ranking.json"
            output.with_suffix(".refresh.lock").write_text("123", encoding="utf-8")
            success, message, metadata = refresh_large_cap_ranking(output, limit=1)
            self.assertFalse(success)
            self.assertIn("already running", message)
            self.assertEqual(metadata, {})

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

    def test_successful_ticker_analysis_is_reused(self):
        cached_ticker_analysis.clear()
        with patch(
            "ticker_analyzer.ui.actions.analyze_ticker",
            return_value={"ticker": "CACHED"},
        ) as analyze:
            first, first_errors = analyze_selected_tickers(["CACHED"], {"Growth": "2Y"}, {})
            second, second_errors = analyze_selected_tickers(["CACHED"], {"Growth": "2Y"}, {})

        self.assertEqual(first, second)
        self.assertEqual(first_errors, second_errors)
        self.assertEqual(analyze.call_count, 1)

    def test_failed_ticker_analysis_is_not_cached(self):
        cached_ticker_analysis.clear()
        with patch(
            "ticker_analyzer.ui.actions.analyze_ticker",
            side_effect=[ValueError("temporary failure"), {"ticker": "RETRY"}],
        ) as analyze:
            first, first_errors = analyze_selected_tickers(["RETRY"], {"Growth": "2Y"}, {})
            second, second_errors = analyze_selected_tickers(["RETRY"], {"Growth": "2Y"}, {})

        self.assertEqual(first, {})
        self.assertIn("RETRY", first_errors)
        self.assertEqual(second, {"RETRY": {"ticker": "RETRY"}})
        self.assertEqual(second_errors, {})
        self.assertEqual(analyze.call_count, 2)


if __name__ == "__main__":
    unittest.main()
