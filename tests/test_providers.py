from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData
from ticker_analyzer.providers import CompositeProvider, SecClient, is_discrete_quarter_or_instant, sec_statement


def empty_market_data(ticker: str, **kwargs):
    values = dict(
        ticker=ticker,
        info={},
        annual_income=pd.DataFrame(),
        annual_balance=pd.DataFrame(),
        annual_cashflow=pd.DataFrame(),
        quarterly_income=pd.DataFrame(),
        quarterly_balance=pd.DataFrame(),
        quarterly_cashflow=pd.DataFrame(),
        growth_history=pd.DataFrame(),
        value_history=pd.DataFrame(),
        analyst_targets={},
        revenue_estimate=pd.DataFrame(),
        earnings_estimate=pd.DataFrame(),
        eps_trend=pd.DataFrame(),
        growth_estimates=pd.DataFrame(),
    )
    values.update(kwargs)
    return MarketData(**values)


class Provider:
    def __init__(self, result):
        self.result = result

    def fetch(self, ticker, ranges):
        return self.result


class ProvidersTest(unittest.TestCase):
    def test_composite_keeps_primary_and_fills_missing_data(self):
        primary = empty_market_data(
            "ABC",
            info={"longName": "Primary Name"},
            official_ids={"cik": "123"},
            provenance={"financials": DataProvenance(provider="SEC", is_primary_source=True)},
        )
        fallback = empty_market_data(
            "ABC",
            info={"longName": "Fallback Name", "currency": "USD"},
            growth_history=pd.DataFrame({"Close": [10.0]}),
            provenance={"prices": DataProvenance(provider="yfinance", fallback_level="secondary_source")},
        )
        result = CompositeProvider([Provider(primary), Provider(fallback)]).fetch("ABC", AnalysisRanges.from_input("2Y"))
        self.assertEqual(result.info["longName"], "Primary Name")
        self.assertEqual(result.info["currency"], "USD")
        self.assertFalse(result.growth_history.empty)
        self.assertEqual(set(result.provenance), {"financials", "prices"})

    def test_sec_requires_contact_user_agent(self):
        with self.assertRaises(ValueError):
            SecClient("anonymous-client")

    def test_sec_statement_keeps_latest_filed_amendment(self):
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "val": 10},
                                {"end": "2025-12-31", "filed": "2026-02-10", "form": "10-K", "val": 11},
                            ]
                        }
                    }
                }
            }
        }
        frame = sec_statement(payload, {"Total Revenue": ["Revenues"]}, forms={"10-K"})
        self.assertEqual(frame.iloc[0, 0], 11)

    def test_sec_quarter_filter_rejects_ytd_duration(self):
        self.assertTrue(is_discrete_quarter_or_instant({"start": "2026-04-01", "end": "2026-06-30"}))
        self.assertFalse(is_discrete_quarter_or_instant({"start": "2026-01-01", "end": "2026-09-30"}))


if __name__ == "__main__":
    unittest.main()
