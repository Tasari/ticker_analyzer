from __future__ import annotations

import unittest
from datetime import UTC, datetime

from ticker_analyzer.watchlist import (
    add_watch_ticker,
    evaluate_watchlist,
    normalize_alerts,
    normalize_snapshots,
    normalize_watchlist,
)


class WatchlistTest(unittest.TestCase):
    def setUp(self):
        self.checked_at = datetime(2026, 8, 27, 12, tzinfo=UTC)

    def test_watchlist_normalizes_tickers_thresholds_and_duplicates(self):
        items = normalize_watchlist(
            [
                {"ticker": " aapl ", "price_above": "200", "score_below": -1},
                {"ticker": "AAPL"},
                {"ticker": "pkn.wa", "score_above": 80},
                {"ticker": "bad ticker"},
            ]
        )

        self.assertEqual([item["ticker"] for item in items], ["AAPL", "PKN.WA"])
        self.assertEqual(items[0]["price_above"], 200)
        self.assertIsNone(items[0]["score_below"])

    def test_add_watch_ticker_is_independent_and_rejects_duplicates_and_account_portfolio(self):
        items, added = add_watch_ticker([], "futu")
        duplicate, duplicate_added = add_watch_ticker(items, "FUTU")
        account, account_added = add_watch_ticker(items, "ACC_STMT")

        self.assertTrue(added)
        self.assertEqual(items[0]["ticker"], "FUTU")
        self.assertFalse(duplicate_added)
        self.assertEqual(duplicate, items)
        self.assertFalse(account_added)
        self.assertEqual(account, items)

    def test_first_refresh_reports_reached_threshold_and_missing_data(self):
        refresh = evaluate_watchlist(
            [{"ticker": "AAPL", "price_above": 150, "score_above": 80}],
            {},
            {
                "AAPL": {
                    "company_name": "Apple",
                    "current_price": 160,
                    "overall_score": 82,
                    "rating": "Buy",
                    "missing": ["Quarterly cash flow"],
                }
            },
            {},
            checked_at=self.checked_at,
        )

        snapshot = refresh.snapshots["AAPL"]
        self.assertEqual(snapshot["price"], 160)
        self.assertEqual(snapshot["score"], 82)
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual([alert["kind"] for alert in refresh.alerts], [
            "threshold", "threshold", "missing_data",
        ])

    def test_refresh_detects_rating_change_crossing_and_data_changes(self):
        items = [{"ticker": "AAPL", "score_below": 70}]
        first = evaluate_watchlist(
            items,
            {},
            {
                "AAPL": {
                    "current_price": 100,
                    "overall_score": 75,
                    "rating": "Buy",
                    "missing": ["Old missing", "Still missing"],
                }
            },
            {},
            checked_at=self.checked_at,
        )
        second = evaluate_watchlist(
            items,
            first.snapshots,
            {
                "AAPL": {
                    "current_price": 95,
                    "overall_score": 68,
                    "rating": "Hold",
                    "missing": ["Still missing", "New missing"],
                }
            },
            {},
            checked_at=self.checked_at,
        )

        kinds = [alert["kind"] for alert in second.alerts]
        self.assertEqual(kinds, ["rating", "threshold", "missing_data", "new_data"])
        self.assertIn("Buy -> Hold", second.alerts[0]["message"])

    def test_error_keeps_last_values_and_enters_retry_until_recovery(self):
        items = [{"ticker": "FUTU"}]
        previous = evaluate_watchlist(
            items,
            {},
            {"FUTU": {"current_price": 100, "overall_score": 90, "rating": "Strong Buy"}},
            {},
            checked_at=self.checked_at,
        )
        failed = evaluate_watchlist(
            items,
            previous.snapshots,
            {},
            {"FUTU": "Yahoo returned 401"},
            checked_at=self.checked_at,
        )

        self.assertEqual(failed.snapshots["FUTU"]["price"], 100)
        self.assertEqual(failed.snapshots["FUTU"]["rating"], "Strong Buy")
        self.assertEqual(failed.retry, {"FUTU": "Yahoo returned 401"})
        self.assertEqual([alert["kind"] for alert in failed.alerts], ["retry"])

        repeated = evaluate_watchlist(
            items,
            failed.snapshots,
            {},
            {"FUTU": "Yahoo returned 401"},
            checked_at=self.checked_at,
        )
        self.assertEqual(repeated.alerts, [])

        recovered = evaluate_watchlist(
            items,
            failed.snapshots,
            {"FUTU": {"current_price": 101, "overall_score": 90, "rating": "Strong Buy"}},
            {},
            checked_at=self.checked_at,
        )
        self.assertEqual(recovered.retry, {})
        self.assertIn("recovered", [alert["kind"] for alert in recovered.alerts])

    def test_persisted_watch_data_is_bounded_and_pruned_to_watched_tickers(self):
        items = normalize_watchlist(["AAPL"])
        snapshots = normalize_snapshots(
            {
                "AAPL": {"price": 100, "score": 80, "status": "ok"},
                "REMOVED": {"price": 50, "status": "ok"},
            },
            items,
        )
        alerts = normalize_alerts(
            [
                {
                    "ticker": "AAPL",
                    "kind": "rating",
                    "message": "Changed",
                    "created_at": self.checked_at.isoformat(),
                }
            ]
        )

        self.assertEqual(list(snapshots), ["AAPL"])
        self.assertEqual(alerts[0]["read"], False)


if __name__ == "__main__":
    unittest.main()
