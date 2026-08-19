from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ticker_analyzer.ranking import (
    UNIVERSE_SCHEMA_VERSION,
    analysis_fingerprint,
    build_large_cap_ranking,
    checkpoint_universe_is_current,
    combine_market_universes,
    fetch_large_cap_universe,
    fetch_large_cap_universe_nasdaq,
    fetch_large_cap_universe_with_retry,
    load_ranking,
    market_counts,
    merge_large_cap_universes,
    normalize_ticker,
    ranking_row,
    save_ranking,
    select_nasdaq_market,
    sort_ranking,
    validate_market_coverage,
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
    def test_only_current_incomplete_universe_checkpoint_is_resumed(self):
        universe = [{"ticker": "A"}]
        current = {
            "metadata": {"complete": False, "universe_schema_version": UNIVERSE_SCHEMA_VERSION},
            "universe": universe,
        }
        stale = {"metadata": {"complete": False}, "universe": universe}
        complete = {
            "metadata": {"complete": True, "universe_schema_version": UNIVERSE_SCHEMA_VERSION},
            "universe": universe,
        }

        self.assertTrue(checkpoint_universe_is_current(current, limit=1))
        self.assertFalse(checkpoint_universe_is_current(stale, limit=1))
        self.assertFalse(checkpoint_universe_is_current(complete, limit=1))

    def test_checkpoint_rejects_missing_required_market(self):
        current = {
            "metadata": {"complete": False, "universe_schema_version": UNIVERSE_SCHEMA_VERSION},
            "universe": [{"ticker": "A", "market": "United States"}],
        }

        self.assertFalse(
            checkpoint_universe_is_current(current, limit=1, required_markets=["Poland"])
        )
        current["universe"].append({"ticker": "PKO.WA", "market": "Poland"})
        self.assertTrue(
            checkpoint_universe_is_current(current, limit=1, required_markets=["Poland"])
        )

    @patch("ticker_analyzer.ranking.time.sleep")
    @patch("ticker_analyzer.ranking.fetch_large_cap_universe")
    def test_regional_universe_retries_empty_response(self, fetch, sleep):
        fetch.side_effect = [[], [{"ticker": "PKO.WA", "market": "Poland"}]]

        result = fetch_large_cap_universe_with_retry(
            100,
            0,
            region="pl",
            country="Poland",
            market="Poland",
            retry_delay=0,
        )

        self.assertEqual(result[0]["ticker"], "PKO.WA")
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(0)

    def test_market_coverage_rejects_missing_market(self):
        universe = [{"ticker": "A", "market": "United States"}]
        self.assertEqual(market_counts(universe), {"United States": 1})
        with self.assertRaisesRegex(RuntimeError, "Poland"):
            validate_market_coverage(universe, ["United States", "Poland"])

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
        self.assertEqual(result[0]["country"], "United States")

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

    def test_market_buckets_keep_us_and_chinese_adrs_separate(self):
        nasdaq = [
            {"ticker": "A", "market_cap": 100, "country": "United States", "market": "United States"},
            {"ticker": "B", "market_cap": 90, "country": "United States", "market": "United States"},
            {"ticker": "FUTU", "market_cap": 80, "country": "China", "market": "United States"},
        ]
        us = select_nasdaq_market(nasdaq, country="United States", market="United States", limit=2)
        china = select_nasdaq_market(nasdaq, country="China", market="China (US ADR)", limit=1)
        yahoo = [{"ticker": "A", "market_cap": 100, "exchange": "NasdaqGS"}]
        poland = [{"ticker": "PKN.WA", "market_cap": 70, "country": "Poland", "market": "Poland"}]

        result = combine_market_universes(
            us, yahoo, china, [poland], us_limit=2, market_limit=1
        )

        self.assertEqual([item["ticker"] for item in result], ["A", "B", "FUTU", "PKN.WA"])
        self.assertEqual(result[0]["exchange"], "NasdaqGS")
        self.assertEqual(result[1]["exchange"], "US-listed")
        self.assertEqual(result[2]["market"], "China (US ADR)")

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
