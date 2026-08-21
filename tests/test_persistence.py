from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from ticker_analyzer.persistence import (
    STORAGE_KEY,
    apply_snapshot,
    build_snapshot,
    hydrate_browser_state,
    parse_snapshot,
    persist_browser_state,
)


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 21, 12, tzinfo=UTC)

    def test_snapshot_round_trip_normalizes_preferences_without_results(self):
        state = {
            "selected_tickers": [" aapl ", "BRK/B", "AAPL", "bad ticker"],
            "active_ticker": "brk/b",
            "growth_range": "3y",
            "fundamentals_range": "invalid",
            "value_range": "1Y",
            "page": "Large Cap Ranking",
            "analysis_results": {"AAPL": {"score": 90}},
        }

        raw = json.dumps(build_snapshot(state, now=self.now))
        restored = parse_snapshot(raw, now=self.now + timedelta(days=2))

        self.assertEqual(restored["selected_tickers"], ["AAPL", "BRK-B"])
        self.assertEqual(restored["active_ticker"], "BRK-B")
        self.assertEqual(restored["ranges"], {"Growth": "3Y", "Fundamentals": "2Y", "Value": "1Y"})
        self.assertEqual(restored["page"], "Large Cap Ranking")
        self.assertNotIn("analysis_results", restored)

    def test_expired_invalid_and_future_snapshots_are_ignored(self):
        valid = build_snapshot({"selected_tickers": ["AAPL"]}, now=self.now)

        self.assertIsNone(parse_snapshot("not json", now=self.now))
        self.assertIsNone(parse_snapshot(json.dumps({**valid, "version": 99}), now=self.now))
        self.assertIsNone(parse_snapshot(json.dumps(valid), now=self.now + timedelta(days=31)))
        self.assertIsNone(parse_snapshot(json.dumps(valid), now=self.now - timedelta(minutes=6)))

    def test_apply_snapshot_resets_stale_analysis_and_sets_widget_state(self):
        state = {"analysis_results": {"OLD": {}}, "analysis_errors": {"OLD": "error"}}
        snapshot = build_snapshot(
            {
                "selected_tickers": ["FUTU", "PKN.WA"],
                "active_ticker": "FUTU",
                "growth_range": "1Y",
                "fundamentals_range": "2Y",
                "value_range": "3Y",
                "page": "Stock Analyzer",
            },
            now=self.now,
        )

        apply_snapshot(state, snapshot)

        self.assertEqual(state["selected_tickers"], ["FUTU", "PKN.WA"])
        self.assertEqual(state["active_ticker"], "FUTU")
        self.assertEqual(state["growth_range"], "1Y")
        self.assertEqual(state["fundamentals_range"], "2Y")
        self.assertEqual(state["value_range"], "3Y")
        self.assertEqual(state["analysis_results"], {})
        self.assertEqual(state["analysis_errors"], {})

    def test_browser_loader_hydrates_once_from_component_response(self):
        raw = json.dumps(build_snapshot({"selected_tickers": ["FUTU"]}, now=self.now))
        state = {}
        with (
            patch("ticker_analyzer.persistence.browser_storage_disabled", return_value=False),
            patch(
                "ticker_analyzer.persistence._BROWSER_STORAGE",
                return_value=SimpleNamespace(response={"loaded": True, "raw": raw}),
            ) as storage,
            patch("ticker_analyzer.persistence.datetime") as clock,
        ):
            clock.now.return_value = self.now
            clock.fromisoformat.side_effect = datetime.fromisoformat
            self.assertTrue(hydrate_browser_state(state))
            self.assertTrue(hydrate_browser_state(state))

        storage.assert_called_once()
        self.assertEqual(state["selected_tickers"], ["FUTU"])

    def test_browser_writer_stores_only_compact_preferences(self):
        state = {
            "_browser_preferences_hydrated": True,
            "selected_tickers": ["FUTU"],
            "active_ticker": "FUTU",
            "analysis_results": {"FUTU": {"large": "result"}},
        }
        with (
            patch("ticker_analyzer.persistence.browser_storage_disabled", return_value=False),
            patch("ticker_analyzer.persistence._BROWSER_STORAGE") as storage,
        ):
            persist_browser_state(state)

        data = storage.call_args.kwargs["data"]
        payload = json.loads(data["payload"])
        self.assertEqual(data["storageKey"], STORAGE_KEY)
        self.assertEqual(payload["selected_tickers"], ["FUTU"])
        self.assertNotIn("analysis_results", payload)


if __name__ == "__main__":
    unittest.main()
