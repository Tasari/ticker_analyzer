from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ticker_analyzer.ranking import (
    analysis_fingerprint,
    build_large_cap_ranking,
    fetch_large_cap_universe,
    fetch_large_cap_universe_nasdaq,
    load_ranking,
    merge_large_cap_universes,
    normalize_ticker,
    ranking_row,
    save_ranking,
    sort_ranking,
)


def analysis(ticker: str, score: float | None, confidence: float = 80) -> dict:
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Inc.",
        "profile": "Industrial",
        "overall_score": score,
        "rating": "Buy" if score is not None else "Insufficient Data",
        "confidence": confidence,
        "data_quality": confidence,
        "scoring_version": 5,
        "config_version": 5,
        "calibration_version": "v5-audit-2026Q3",
        "tabs": {
            name: {"score": score, "coverage": {"percentage": 90}}
            for name in ("Growth", "Fundamentals", "Value")
        },
    }


class RankingTest(unittest.TestCase):
    @patch("ticker_analyzer.ranking.yf.screen")
    @patch("ticker_analyzer.ranking.yf.EquityQuery")
    def test_yahoo_universe_deduplicates_and_normalizes(self, query, screen):
        query.return_value = object()
        screen.return_value = {
            "quotes": [
                {"symbol": "brk/b", "longName": "Berkshire", "marketCap": 10, "exchange": "NYQ"},
                {"symbol": "BRK-B", "marketCap": 10},
                {"symbol": "MSFT", "shortName": "Microsoft", "marketCap": 9},
            ]
        }
        result = fetch_large_cap_universe(limit=3)
        self.assertEqual([item["ticker"] for item in result], ["BRK-B", "MSFT"])
        self.assertEqual(result[0]["company_name"], "Berkshire")

    @patch("ticker_analyzer.ranking.requests.get")
    def test_nasdaq_universe_filters_bad_caps_and_sorts(self, get):
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {
            "data": {"rows": [
                {"symbol": "B", "name": "B", "marketCap": "2"},
                {"symbol": "A", "name": "A", "marketCap": "10"},
                {"symbol": "BAD", "marketCap": "n/a"},
            ]}
        }
        self.assertEqual([item["ticker"] for item in fetch_large_cap_universe_nasdaq(2)], ["A", "B"])

    def test_normalize_ticker_converts_nasdaq_share_class_separator(self):
        self.assertEqual(normalize_ticker("brk/b"), "BRK-B")

    def test_merge_universes_keeps_us_listed_foreign_company(self):
        yahoo = [
            {"ticker": "MSFT", "market_cap": 100, "sector": "Technology"},
            {"ticker": "AAPL", "market_cap": 90},
        ]
        nasdaq = [
            {"ticker": "FUTU", "market_cap": 95, "country": "China"},
            {"ticker": "MSFT", "market_cap": 99, "exchange": "NASDAQ"},
        ]

        result = merge_large_cap_universes(nasdaq, yahoo, limit=3)

        self.assertEqual([item["ticker"] for item in result], ["MSFT", "FUTU", "AAPL"])
        self.assertEqual(result[0]["sector"], "Technology")
        self.assertEqual(result[0]["exchange"], "NASDAQ")

    def test_ranking_row_keeps_market_cap_and_scores(self):
        row = ranking_row({"ticker": "AAA", "market_cap": 10}, analysis("AAA", 72))
        self.assertEqual(row["market_cap"], 10)
        self.assertEqual(row["overall_score"], 72)
        self.assertEqual(row["growth_coverage"], 90)

    def test_sort_places_unrated_companies_last(self):
        rows = [ranking_row({"ticker": ticker}, analysis(ticker, score)) for ticker, score in [("A", 50), ("B", None), ("C", 80)]]
        ordered = sort_ranking(rows)
        self.assertEqual([row["ticker"] for row in ordered], ["C", "A", "B"])
        self.assertEqual([row["rank"] for row in ordered], [1, 2, None])

    def test_build_resumes_existing_rows_and_records_errors(self):
        universe = [{"ticker": ticker} for ticker in ("A", "B", "C")]
        config = {"version": 5, "calibration_version": "v5-audit-2026Q3"}
        fingerprint = analysis_fingerprint(config, "2026-07-31")
        existing = {
            "metadata": fingerprint,
            "companies": [ranking_row(universe[0], analysis("A", 60), fingerprint)],
            "errors": [],
        }
        calls = []

        def analyzer(ticker, ranges, config):
            calls.append(ticker)
            if ticker == "C":
                raise ValueError("provider failed")
            return analysis(ticker, 70)

        result = build_large_cap_ranking(
            universe, config, existing=existing, analyzer=analyzer, workers=2, retries=0,
            data_as_of="2026-07-31",
        )
        self.assertEqual(set(calls), {"B", "C"})
        self.assertEqual(result["metadata"]["analyzed"], 2)
        self.assertEqual(result["metadata"]["failed"], 1)

    def test_changed_config_digest_invalidates_checkpoint(self):
        universe = [{"ticker": "A"}]
        old = {"version": 5, "calibration_version": "v5-audit-2026Q3", "threshold": 1}
        fingerprint = analysis_fingerprint(old, "2026-07-31")
        existing = {
            "metadata": fingerprint,
            "companies": [ranking_row(universe[0], analysis("A", 60), fingerprint)],
            "errors": [],
        }
        calls = []

        def analyzer(ticker, _ranges, _config):
            calls.append(ticker)
            return analysis(ticker, 70)

        new = {**old, "threshold": 2}
        build_large_cap_ranking(
            universe, new, existing=existing, analyzer=analyzer, retries=0,
            data_as_of="2026-07-31",
        )
        self.assertEqual(calls, ["A"])

    def test_save_and_load_are_atomic_from_callers_perspective(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.json"
            payload = {"metadata": {"complete": True}, "companies": [], "errors": []}
            save_ranking(payload, path)
            self.assertEqual(load_ranking(path), payload)


if __name__ == "__main__":
    unittest.main()
