from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData
from ticker_analyzer.providers import (
    CompositeProvider,
    FdicClient,
    FinraClient,
    GleifClient,
    JsonApiClient,
    NbpClient,
    SecClient,
    SecCompanyFactsProvider,
    latest_sec_filing,
    sec_statement,
)


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


class RecordingClient:
    def get_json(self, url, **kwargs):
        self.call = (url, kwargs)
        return {"rates": [{"mid": 4.0}]}


class ApiResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self.payload


class ApiSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def mount(self, *_args):
        pass

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class ProvidersTest(unittest.TestCase):
    def test_json_client_uses_etag_cache_and_distinguishes_params(self):
        session = ApiSession(
            [
                ApiResponse({"value": 1}, headers={"ETag": "one"}),
                ApiResponse({}, status_code=304),
                ApiResponse({"value": 2}, headers={"ETag": "two"}),
            ]
        )
        client = JsonApiClient(session=session, minimum_interval=0)
        self.assertEqual(client.get_json("https://example.test/data", params={"page": 1})["value"], 1)
        self.assertEqual(client.get_json("https://example.test/data", params={"page": 1})["value"], 1)
        self.assertEqual(client.get_json("https://example.test/data", params={"page": 2})["value"], 2)
        self.assertEqual(session.calls[1][1]["headers"]["If-None-Match"], "one")

    def test_json_client_retries_rate_limits_and_transient_server_errors(self):
        client = JsonApiClient(minimum_interval=0)
        retry = client.session.get_adapter("https://").max_retries
        self.assertTrue({429, 503}.issubset(set(retry.status_forcelist)))
        self.assertTrue(retry.respect_retry_after_header)

    def test_json_client_bounds_etag_cache(self):
        session = ApiSession(
            [
                ApiResponse({"value": 1}, headers={"ETag": "one"}),
                ApiResponse({"value": 2}, headers={"ETag": "two"}),
            ]
        )
        client = JsonApiClient(session=session, minimum_interval=0, max_cache_entries=1)

        client.get_json("https://example.test/one")
        client.get_json("https://example.test/two")

        self.assertEqual(len(client._cache), 1)
        self.assertIn("/two", next(iter(client._cache)))

    def test_regulatory_client_methods_build_expected_requests(self):
        for client, method, kwargs, fragment in [
            (NbpClient(minimum_interval=0), "exchange_rate", {"currency": "USD"}, "exchangerates"),
            (FdicClient(minimum_interval=0), "institutions", {"cert": 1}, "institutions"),
            (FdicClient(minimum_interval=0), "financials", {"cert": 1}, "financials"),
            (GleifClient(minimum_interval=0), "lei_records", {"legal_name": "ABC"}, "lei-records"),
        ]:
            recorder = RecordingClient()
            client.get_json = recorder.get_json
            getattr(client, method)(**kwargs)
            self.assertIn(fragment, recorder.call[0])
        nbp = NbpClient(minimum_interval=0)
        recorder = RecordingClient()
        nbp.get_json = recorder.get_json
        self.assertEqual(nbp.exchange_rates("USD", "2026-01-01", "2026-01-02"), [{"mid": 4.0}])

    def test_sec_provider_fetches_relevant_statements_and_identity(self):
        annual = {"start": "2025-01-01", "end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "val": 100}
        quarter = {"start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "form": "10-Q", "val": 30}
        payload = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [annual, quarter]}}}}}

        class FakeSec:
            def ticker_map(self):
                return {"0": {"ticker": "ABC", "cik_str": 123, "title": "ABC Corp"}}

            def company_facts(self, _cik):
                return payload

            def submissions(self, _cik):
                return {"name": "ABC Corp", "sic": "3571"}

        result = SecCompanyFactsProvider(FakeSec()).fetch("ABC", AnalysisRanges.from_input("2Y"))
        self.assertEqual(result.info["longName"], "ABC Corp")
        self.assertEqual(result.annual_income.loc["Total Revenue"].iloc[0], 100)
        self.assertEqual(result.quarterly_income.loc["Total Revenue"].iloc[-1], 30)
        self.assertEqual(result.provenance["financials"].form, "10-Q")
        with self.assertRaisesRegex(ValueError, "CIK"):
            SecCompanyFactsProvider(FakeSec()).fetch("MISSING", AnalysisRanges.from_input("2Y"))
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

    def test_composite_merges_statement_cells_without_overwriting_primary(self):
        period = pd.Timestamp("2025-12-31")
        primary = empty_market_data(
            "ABC", annual_income=pd.DataFrame({period: {"Total Revenue": 100.0}})
        )
        fallback = empty_market_data(
            "ABC",
            annual_income=pd.DataFrame(
                {period: {"Total Revenue": 99.0, "EBITDA": 25.0}}
            ),
        )
        result = CompositeProvider([Provider(primary), Provider(fallback)]).fetch(
            "ABC", AnalysisRanges.from_input("2Y")
        )
        self.assertEqual(result.annual_income.at["Total Revenue", period], 100.0)
        self.assertEqual(result.annual_income.at["EBITDA", period], 25.0)
        self.assertAlmostEqual(result.annual_income.attrs["reconciliation"][0]["relative_difference"], 0.01)

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
        versions = frame.attrs["observation_provenance"][("Total Revenue", pd.Timestamp("2025-12-31"))]
        self.assertEqual(len(versions), 2)

    def test_sec_statement_honors_analysis_date_for_restatements(self):
        payload = self._revenue_payload()
        before = sec_statement(
            payload, {"Total Revenue": ["Revenues"]}, forms={"10-K", "10-K/A"}, as_of="2026-02-05"
        )
        after = sec_statement(
            payload, {"Total Revenue": ["Revenues"]}, forms={"10-K", "10-K/A"}, as_of="2026-02-15"
        )
        self.assertEqual(before.iloc[0, 0], 10)
        self.assertEqual(after.iloc[0, 0], 11)

    @staticmethod
    def _revenue_payload():
        return {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {"end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "val": 10},
                                {"end": "2025-12-31", "filed": "2026-02-10", "form": "10-K/A", "val": 11},
                            ]
                        }
                    }
                }
            }
        }

    def test_sec_ytd_values_are_derived_into_discrete_quarters(self):
        facts = [
            {"start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "form": "10-Q", "val": 10},
            {"start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "val": 25},
            {"start": "2026-01-01", "end": "2026-09-30", "filed": "2026-11-01", "form": "10-Q", "val": 45},
            {"start": "2026-01-01", "end": "2026-12-31", "filed": "2027-02-01", "form": "10-K", "val": 70},
        ]
        payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": facts}}}}}
        frame = sec_statement(
            payload,
            {"Net Income": ["NetIncomeLoss"]},
            forms={"10-Q", "10-K"},
            quarterly=True,
        )
        self.assertEqual(list(frame.loc["Net Income"]), [10, 15, 20, 25])

    def test_sec_ytd_missing_q1_does_not_invent_q2(self):
        facts = [
            {"start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q", "val": 25},
            {"start": "2026-01-01", "end": "2026-09-30", "filed": "2026-11-01", "form": "10-Q", "val": 45},
        ]
        payload = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": facts}}}}}
        frame = sec_statement(payload, {"Net Income": ["NetIncomeLoss"]}, forms={"10-Q"}, quarterly=True)
        self.assertEqual(list(frame.columns), [pd.Timestamp("2026-09-30")])
        self.assertEqual(frame.iloc[0, 0], 20)

    def test_total_debt_sums_current_noncurrent_and_lease_components(self):
        def fact(value):
            return {"units": {"USD": [{"end": "2025-12-31", "filed": "2026-02-01", "form": "10-K", "val": value}]}}

        payload = {"facts": {"us-gaap": {
            "LongTermDebtCurrent": fact(10),
            "LongTermDebtNoncurrent": fact(70),
            "FinanceLeaseLiabilityCurrent": fact(2),
            "FinanceLeaseLiabilityNoncurrent": fact(8),
        }}}
        frame = sec_statement(payload, {"Total Debt": ["LongTermDebtAndFinanceLeaseObligations"]}, forms={"10-K"})
        self.assertEqual(frame.iloc[0, 0], 90)

    def test_complete_total_debt_fact_wins_over_components(self):
        def fact(value, tag_form="10-K"):
            return {"units": {"USD": [{"end": "2025-12-31", "filed": "2026-02-01", "form": tag_form, "val": value}]}}

        payload = {"facts": {"us-gaap": {
            "LongTermDebtAndFinanceLeaseObligations": fact(80),
            "LongTermDebtCurrent": fact(10),
            "LongTermDebtNoncurrent": fact(70),
            "FinanceLeaseLiabilityCurrent": fact(2),
        }}}
        frame = sec_statement(payload, {"Total Debt": ["LongTermDebtAndFinanceLeaseObligations"]}, forms={"10-K"})
        self.assertEqual(frame.iloc[0, 0], 80)

    def test_latest_sec_filing_ignores_unrelated_forms(self):
        payload = {"facts": {"us-gaap": {
            "Revenue": {"units": {"USD": [
                {"filed": "2026-02-01", "form": "10-K"},
                {"filed": "2026-07-01", "form": "8-K"},
            ]}}
        }}}
        self.assertEqual(latest_sec_filing(payload, forms={"10-K"})["filed"], "2026-02-01")

    def test_latest_sec_filing_honors_data_as_of(self):
        payload = {"facts": {"us-gaap": {
            "Revenue": {"units": {"USD": [
                {"filed": "2026-02-01", "form": "10-K"},
                {"filed": "2026-05-01", "form": "10-Q"},
            ]}}
        }}}
        filing = latest_sec_filing(
            payload, forms={"10-K", "10-Q"}, as_of=pd.Timestamp("2026-03-01", tz="UTC")
        )
        self.assertEqual(filing["filed"], "2026-02-01")

    def test_finra_uses_production_dataset_unless_sandbox_is_explicit(self):
        production = FinraClient(minimum_interval=0)
        sandbox = FinraClient(sandbox=True, minimum_interval=0)
        production_call = RecordingClient()
        sandbox_call = RecordingClient()
        production.get_json = production_call.get_json
        sandbox.get_json = sandbox_call.get_json
        production.broker_dealers(crd_number=1)
        sandbox.broker_dealers(crd_number=1)
        self.assertIn("brokerDealerFirmList", production_call.call[0])
        self.assertNotIn("Mock", production_call.call[0])
        self.assertIn("Mock", sandbox_call.call[0])


if __name__ == "__main__":
    unittest.main()
