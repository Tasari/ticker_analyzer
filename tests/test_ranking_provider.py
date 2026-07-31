from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.domain import AnalysisRanges
from ticker_analyzer.ranking_provider import (
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
    def test_public_provider_builds_market_data_from_public_payloads(self):
        statements = {
            "timeseries": {
                "result": [
                    {
                        "meta": {"type": ["annualTotalRevenue"]},
                        "annualTotalRevenue": [
                            {"asOfDate": "2025-12-31", "reportedValue": {"raw": 100.0}}
                        ],
                    },
                    {
                        "meta": {"type": ["annualOrdinarySharesNumber"]},
                        "annualOrdinarySharesNumber": [
                            {"asOfDate": "2025-12-31", "reportedValue": {"raw": 10.0}}
                        ],
                    },
                    {"meta": {"type": ["unknownMetric"]}, "unknownMetric": []},
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
            {"ABC": {"company_name": "ABC Corp", "market_cap": 1000, "sector": "Tech"}}
        )
        provider.session = FakeSession([statements, chart])

        result = provider.fetch("ABC", AnalysisRanges.from_input("3Y"))

        self.assertEqual(result.info["sharesOutstanding"], 10.0)
        self.assertEqual(result.info["currentPrice"], 10.0)
        self.assertEqual(result.annual_income.iloc[0, 0], 100.0)
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


if __name__ == "__main__":
    unittest.main()
