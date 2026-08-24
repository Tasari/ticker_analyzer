from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import wait as futures_wait
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.build_large_cap_ranking import SMOKE_OUTPUT_PATH, configure_run
from ticker_analyzer.ranking import (
    DEFAULT_RANKING_PATH,
    UNIVERSE_SCHEMA_VERSION,
    XTB_EUROPE_MARKETS,
    analysis_fingerprint,
    build_large_cap_ranking,
    checkpoint_universe_is_current,
    combine_market_universes,
    export_ranking,
    fetch_large_cap_universe,
    fetch_large_cap_universe_nasdaq,
    fetch_tradingview_market_universe,
    import_ranking,
    load_ranking,
    market_counts,
    merge_large_cap_universes,
    normalize_ticker,
    ranking_row,
    save_ranking,
    select_nasdaq_market,
    sort_ranking,
    validate_market_coverage,
    yahoo_ticker_from_tradingview,
)
from ticker_analyzer.ranking_quality import build_ranking_quality_report
from ticker_analyzer.ui.ranking_view import ranking_export_filename


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
    def test_smoke_run_has_bounded_quotas_workers_and_separate_output(self):
        args = SimpleNamespace(
            smoke=True,
            limit=1000,
            market_limit=100,
            workers=8,
            output=DEFAULT_RANKING_PATH,
        )

        markets = configure_run(args)

        self.assertEqual(args.limit, 20)
        self.assertEqual(args.market_limit, 5)
        self.assertEqual(args.workers, 3)
        self.assertEqual(args.output, SMOKE_OUTPUT_PATH)
        self.assertEqual(list(markets), ["Poland", "United Kingdom", "Germany"])

    def test_normal_run_keeps_all_market_settings(self):
        output = Path("custom.json")
        args = SimpleNamespace(smoke=False, limit=12, market_limit=4, workers=2, output=output)

        markets = configure_run(args)

        self.assertEqual((args.limit, args.market_limit, args.workers, args.output), (12, 4, 2, output))
        self.assertEqual(markets, XTB_EUROPE_MARKETS)

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

    @patch("ticker_analyzer.ranking_universe.requests.post")
    def test_tradingview_universe_maps_symbol_and_metadata(self, post):
        post.return_value.raise_for_status.return_value = None
        post.return_value.json.return_value = {
            "totalCount": 1,
            "data": [{"s": "GPW:PKN", "d": [
                "PKN", "ORLEN", 100, "GPW", "Energy", "Oil Refining", "Poland", "stock"
            ]}],
        }

        result = fetch_tradingview_market_universe(
            100,
            scanner_market="poland",
            country="Poland",
            market="Poland",
            yahoo_suffix=".WA",
        )

        self.assertEqual(result[0]["ticker"], "PKN.WA")
        self.assertEqual(result[0]["company_name"], "ORLEN")
        self.assertEqual(result[0]["universe_source"], "TradingView stock screener")
        request = post.call_args.kwargs
        self.assertEqual(request["json"]["range"], [0, 100])
        self.assertEqual(request["json"]["sort"]["sortBy"], "market_cap_basic")

    def test_tradingview_symbols_are_mapped_to_yahoo_format(self):
        self.assertEqual(yahoo_ticker_from_tradingview("GPW:PKN", ".WA"), "PKN.WA")
        self.assertEqual(yahoo_ticker_from_tradingview("LSE:BT.A", ".L"), "BT-A.L")
        self.assertEqual(yahoo_ticker_from_tradingview("LSE:RR.", ".L"), "RR.L")
        self.assertEqual(yahoo_ticker_from_tradingview("OMXCOP:MAERSK_B", ".CO"), "MAERSK-B.CO")

    def test_market_coverage_rejects_missing_market(self):
        universe = [{"ticker": "A", "market": "United States"}]
        self.assertEqual(market_counts(universe), {"United States": 1})
        with self.assertRaisesRegex(RuntimeError, "Poland"):
            validate_market_coverage(universe, ["United States", "Poland"])

    @patch("ticker_analyzer.ranking_universe.yf.screen")
    @patch("ticker_analyzer.ranking_universe.yf.EquityQuery")
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

    @patch("ticker_analyzer.ranking_universe.requests.get")
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
            {"ticker": "FUTU", "market_cap": 80, "country": "Hong Kong", "market": "United States"},
        ]
        us = select_nasdaq_market(nasdaq, country="United States", market="United States", limit=2)
        china = select_nasdaq_market(
            nasdaq, country=("China", "Hong Kong"), market="China (US ADR)", limit=1
        )
        yahoo = [{"ticker": "A", "market_cap": 100, "exchange": "NasdaqGS"}]
        poland = [{"ticker": "PKN.WA", "market_cap": 70, "country": "Poland", "market": "Poland"}]

        result = combine_market_universes(
            us, yahoo, china, [poland], us_limit=2, market_limit=1
        )

        self.assertEqual([item["ticker"] for item in result], ["A", "B", "FUTU", "PKN.WA"])
        self.assertEqual(result[0]["exchange"], "NasdaqGS")
        self.assertEqual(result[1]["exchange"], "US-listed")
        self.assertEqual(result[2]["market"], "China (US ADR)")
        self.assertEqual(result[2]["country"], "Hong Kong")

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
        self.assertEqual(result["universe"], [])

    def test_build_keeps_only_worker_count_futures_in_memory(self):
        universe = [{"ticker": str(index)} for index in range(12)]
        config = {"version": 5, "calibration_version": "v5-audit-2026Q3"}

        with patch("ticker_analyzer.ranking_builder.wait", wraps=futures_wait) as bounded_wait:
            result = build_large_cap_ranking(
                universe,
                config,
                analyzer=lambda ticker, _ranges, _config: analysis(ticker, 70),
                workers=2,
                retries=0,
                data_as_of="2026-07-31",
            )

        self.assertEqual(result["metadata"]["analyzed"], 12)
        self.assertTrue(bounded_wait.called)
        self.assertTrue(all(len(call.args[0]) <= 2 for call in bounded_wait.call_args_list))

    def test_large_build_limits_full_checkpoint_writes(self):
        universe = [{"ticker": str(index)} for index in range(60)]
        checkpoints = []

        build_large_cap_ranking(
            universe,
            {"version": 5},
            analyzer=lambda ticker, _ranges, _config: analysis(ticker, 70),
            workers=2,
            retries=0,
            checkpoint=checkpoints.append,
            data_as_of="2026-07-31",
        )

        self.assertEqual(len(checkpoints), 2)
        self.assertEqual([item["metadata"]["processed"] for item in checkpoints], [25, 50])

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

    def test_ranking_load_is_cached_and_save_invalidates_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.json"
            first_payload = {"metadata": {"version": 1}, "companies": [], "errors": []}
            save_ranking(first_payload, path)

            first = load_ranking(path)
            self.assertIs(load_ranking(path), first)

            second_payload = {"metadata": {"version": 2}, "companies": [], "errors": []}
            save_ranking(second_payload, path)
            self.assertEqual(load_ranking(path), second_payload)
            self.assertIsNot(load_ranking(path), first)

    def test_ranking_snapshot_export_import_validates_and_round_trips(self):
        payload = {
            "metadata": {"complete": True},
            "companies": [{"ticker": "AAA", "overall_score": 72}],
            "errors": [],
            "universe": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.json"
            imported = import_ranking(export_ranking(payload), path)
            self.assertEqual(imported, payload)
            self.assertEqual(load_ranking(path), payload)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                import_ranking(
                    json.dumps({**payload, "companies": [{"ticker": "AAA"}, {"ticker": "aaa"}]}).encode(),
                    path,
                )

    def test_quality_report_compares_market_coverage_and_previous_ranking(self):
        previous = {
            "metadata": {},
            "companies": [
                {"ticker": "AAA", "rank": 1, "overall_score": 80, "rating": "Buy", "market": "USA"},
                {"ticker": "OLD", "rank": 2, "overall_score": 60, "rating": "Hold", "market": "USA"},
            ],
            "errors": [],
        }
        current = {
            "metadata": {
                "complete": True,
                "requested": 3,
                "processed": 3,
                "market_counts": {"USA": 2, "Poland": 1},
            },
            "companies": [
                {"ticker": "AAA", "rank": 2, "overall_score": 68, "rating": "Hold", "market": "USA"},
                {"ticker": "NEW", "rank": 1, "overall_score": 90, "rating": "Strong Buy", "market": "USA"},
            ],
            "errors": [{"ticker": "PKO.WA", "error": "429 Too Many Requests"}],
        }

        report = build_ranking_quality_report(current, previous)

        self.assertEqual(report["error_categories"], {"rate_limited": 1})
        self.assertTrue(any("Poland coverage" in warning for warning in report["warnings"]))
        self.assertEqual(report["comparison"]["added"], 1)
        self.assertEqual(report["comparison"]["removed"], 1)
        self.assertEqual(report["comparison"]["large_score_changes"], 1)

    def test_export_filename_contains_readable_snapshot_date_and_time(self):
        self.assertEqual(
            ranking_export_filename({"generated_at": "2026-08-24T21:15:30.123456+00:00"}),
            "large_cap_ranking_2026-08-24_21-15-30_UTC.json",
        )
        self.assertEqual(
            ranking_export_filename({"generated_at": "invalid"}),
            "large_cap_ranking_snapshot.json",
        )


if __name__ == "__main__":
    unittest.main()
