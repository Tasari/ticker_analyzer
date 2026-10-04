from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pandas as pd
import requests
from ticker_analyzer.domain import AnalysisRanges
from ticker_analyzer.ranking.provider import (
    PublicYahooRankingProvider,
    _latest_value,
    _statement_frame,
    _years,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)

    def get(self, *_args, **_kwargs):
        return FakeResponse(next(self.responses))


class RankingProviderTest(unittest.TestCase):
    def test_statement_failure_does_not_discard_available_prices(self):
        provider = PublicYahooRankingProvider({})
        history = pd.DataFrame({"Close": [10.]}, index=pd.to_datetime(["2026-01-01"]))
        with (
            patch.object(provider, "_statements", side_effect=requests.HTTPError("429 Too Many Requests")),
            patch.object(provider, "_history", return_value=(history, history, {"currency": "USD", "regularMarketPrice": 10.})),
        ):
            result = provider.fetch("NVDA", AnalysisRanges.from_input("2Y"))

        self.assertEqual(result.info["currentPrice"], 10.)
        self.assertFalse(result.value_history.empty)
        self.assertTrue(result.annual_income.empty)
        self.assertTrue(any("429" in item["message"] for item in result.diagnostics))
        self.assertEqual(result.provenance["financials"].observation_count, 0)

    def test_price_failure_does_not_discard_statements_needed_by_value(self):
        from tests.test_analysis_engine import FakeProvider, market_data
        from ticker_analyzer.analysis.engine import StockAnalysisEngine
        from ticker_analyzer.config import load_config
        from ticker_analyzer.providers.market_data import fill_missing_core_data

        primary = market_data()
        primary.annual_cashflow = primary.quarterly_cashflow = pd.DataFrame()
        fallback_data = market_data()
        statements = {
            name: getattr(fallback_data, field)
            for name, field in (
                ("income", "annual_income"), ("balance", "annual_balance"), ("cashflow", "annual_cashflow"),
                ("quarterly_income", "quarterly_income"), ("quarterly_balance", "quarterly_balance"),
                ("quarterly_cashflow", "quarterly_cashflow"),
            )
        }
        with (
            patch.object(PublicYahooRankingProvider, "_statements", return_value=statements),
            patch.object(PublicYahooRankingProvider, "_history", side_effect=requests.Timeout("price endpoint timed out")),
        ):
            fill_missing_core_data(primary, AnalysisRanges.from_input("2Y"))

        result = StockAnalysisEngine(provider=FakeProvider(primary)).analyze("NVDA", "2Y", load_config())
        self.assertIsNotNone(result.tabs["Value"]["score"])
        self.assertTrue(any(item["kind"] == "network_error" for item in result.diagnostics))

    def test_profile_enrichment_uses_exact_search_match(self):
        provider = PublicYahooRankingProvider({}, enrich_profile=True)
        provider.session = FakeSession(
            [
                {
                    "quotes": [
                        {"symbol": "OTHER", "sector": "Technology"},
                        {
                            "symbol": "BGEO.L",
                            "longname": "Lion Finance Group PLC",
                            "sector": "Financial Services",
                            "industry": "Banks - Regional",
                        },
                    ]
                },
                {"timeseries": {"result": []}},
                {"chart": {"result": []}},
            ]
        )

        result = provider.fetch("BGEO.L", AnalysisRanges.from_input("2Y"))

        self.assertEqual(result.info["longName"], "Lion Finance Group PLC")
        self.assertEqual(result.info["sector"], "Financial Services")
        self.assertEqual(result.info["industry"], "Banks - Regional")
    def test_public_provider_builds_market_data_from_public_payloads(self):
        statements = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"type": ["annualTotalRevenue"]},
                        "annualTotalRevenue": [
                            {"asOfDate": "2025-12-31", "currencyCode": "USD", "reportedValue": {"raw": 100.0}}
                        ],
                    },
                    {
                        "meta": {"type": ["annualOrdinarySharesNumber"]},
                        "annualOrdinarySharesNumber": [
                            {"asOfDate": "2025-12-31", "reportedValue": {"raw": 10.0}}
                        ],
                    },
                    {"meta": {"type": ["unknownMetric"]}, "unknownMetric": []},
                    {
                        "meta": {"type": ["quarterlyNetIncome"]},
                        "quarterlyNetIncome": [
                            {"asOfDate": "2026-03-31", "currencyCode": "USD", "reportedValue": {"raw": 12.0}}
                        ],
                    },
                ]
            }
        }
        chart = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1767139200, 1769817600],
                        "indicators": {
                            "quote": [{"close": [9.0, 10.0]}],
                            "adjclose": [{"adjclose": [8.5, 9.5]}],
                        },
                        "meta": {"regularMarketPrice": 10.0, "currency": "USD"},
                    }
                ]
            }
        }
        provider = PublicYahooRankingProvider(
            {"ABC": {"company_name": "ABC Corp", "market_cap": 1000, "market_cap_currency": "USD", "sector": "Tech"}}
        )
        provider.session = FakeSession([statements, chart])

        result = provider.fetch("ABC", AnalysisRanges.from_input("3Y"))

        self.assertEqual(result.info["sharesOutstanding"], 10.0)
        self.assertEqual(result.info["currentPrice"], 10.0)
        self.assertEqual(result.annual_income.iloc[0, 0], 100.0)
        self.assertEqual(result.annual_income.attrs["financial_currency"], "USD")
        self.assertEqual(result.quarterly_income.loc["Net Income", pd.Timestamp("2026-03-31")], 12.0)
        self.assertEqual(result.quarterly_income.attrs["financial_currency"], "USD")
        self.assertTrue(result.quarterly_balance.empty)
        self.assertEqual(result.info["marketCap"], 1000)
        self.assertEqual(list(result.growth_history["Close"]), [8.5, 9.5])
        self.assertEqual(list(result.value_history["Close"]), [9.0, 10.0])
        self.assertEqual(result.provenance["financials"].provider, "Yahoo Finance public")
        self.assertFalse(result.provenance["financials"].is_primary_source)
        self.assertEqual(result.diagnostics[0]["kind"], "fallback")

    def test_history_falls_back_to_raw_close_and_empty_chart(self):
        provider = PublicYahooRankingProvider({})
        provider.session = FakeSession([
            {"chart": {"result": [{"timestamp": [1767139200], "indicators": {"quote": [{"close": [7.0]}]}, "meta": {}}]}},
            {"chart": {"result": None}},
        ])
        frame, raw, _ = provider._history("ABC", 0)
        empty, empty_raw, metadata = provider._history("ABC", 2)
        self.assertEqual(frame.iloc[0, 0], 7.0)
        self.assertEqual(raw.iloc[0, 0], 7.0)
        self.assertTrue(empty.empty)
        self.assertTrue(empty_raw.empty)
        self.assertEqual(metadata, {})

    def test_helpers_handle_empty_and_invalid_values(self):
        self.assertTrue(_statement_frame({}).empty)
        frame = _statement_frame({"Revenue": {pd.Timestamp("2025-12-31"): 4.0}})
        self.assertEqual(_latest_value(frame, "Revenue"), 4.0)
        self.assertIsNone(_latest_value(frame, "Missing"))
        self.assertIsNone(_latest_value(pd.DataFrame(), "Revenue"))
        self.assertEqual(_years("5Y"), 5)
        self.assertEqual(_years("bad"), 3)

    def test_public_provider_uses_one_session_per_worker_thread(self):
        provider = PublicYahooRankingProvider({})
        created = []

        class Session:
            headers = {}

        def make_session():
            session = Session()
            created.append(session)
            return session

        barrier = Barrier(2)

        def worker_session(_index):
            session = provider.session
            barrier.wait(timeout=1)
            return session

        with patch("ticker_analyzer.ranking.provider.requests.Session", side_effect=make_session):
            with ThreadPoolExecutor(max_workers=2) as executor:
                sessions = list(executor.map(worker_session, range(2)))

        self.assertEqual(len(created), 2)
        self.assertIsNot(sessions[0], sessions[1])

    def test_explicit_session_override_is_shared_for_test_clients(self):
        provider = PublicYahooRankingProvider({})
        injected = object()

        provider.session = injected

        self.assertIs(provider.session, injected)


if __name__ == "__main__":
    unittest.main()
