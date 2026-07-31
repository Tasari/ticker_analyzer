from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ticker_analyzer.ranking import (
    build_large_cap_ranking,
    load_ranking,
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
        "scoring_version": 3,
        "config_version": 3,
        "tabs": {
            name: {"score": score, "coverage": {"percentage": 90}}
            for name in ("Growth", "Fundamentals", "Value")
        },
    }


class RankingTest(unittest.TestCase):
    def test_normalize_ticker_converts_nasdaq_share_class_separator(self):
        self.assertEqual(normalize_ticker("brk/b"), "BRK-B")

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
        existing = {"companies": [ranking_row(universe[0], analysis("A", 60))], "errors": []}
        calls = []

        def analyzer(ticker, ranges, config):
            calls.append(ticker)
            if ticker == "C":
                raise ValueError("provider failed")
            return analysis(ticker, 70)

        result = build_large_cap_ranking(universe, {}, existing=existing, analyzer=analyzer, workers=2, retries=0)
        self.assertEqual(set(calls), {"B", "C"})
        self.assertEqual(result["metadata"]["analyzed"], 2)
        self.assertEqual(result["metadata"]["failed"], 1)

    def test_save_and_load_are_atomic_from_callers_perspective(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.json"
            payload = {"metadata": {"complete": True}, "companies": [], "errors": []}
            save_ranking(payload, path)
            self.assertEqual(load_ranking(path), payload)


if __name__ == "__main__":
    unittest.main()
