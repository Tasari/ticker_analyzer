from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.analysis.fair_value import inputs_from_analysis
from ticker_analyzer.analysis.valuation_basis import prepare_valuation_basis, share_basis
from ticker_analyzer.config import load_config
from ticker_analyzer.metrics.valuation import build_historical_ratio_context, fcf_yield
from ticker_analyzer.providers.merge import merge_market_data
from ticker_analyzer.providers.sec import empty_market_data


def example(ticker="TSM", quote="USD", reporting="TWD"):
    dates = pd.to_datetime(["2024-01-31", "2024-02-29"])
    return empty_market_data(
        ticker,
        info={"currency": quote, "financialCurrency": reporting, "currentPrice": 100,
              "marketCap": 2000, "longName": "Example", "quoteType": "EQUITY"},
        annual_income=pd.DataFrame({pd.Timestamp("2023-01-01"): [2000, 1000]}, index=["Total Revenue", "Net Income"]),
        annual_balance=pd.DataFrame({pd.Timestamp("2023-01-01"): [100, 200, 20]}, index=["Ordinary Shares Number", "Total Debt", "Cash And Cash Equivalents"]),
        annual_cashflow=pd.DataFrame({pd.Timestamp("2023-01-01"): [400]}, index=["Free Cash Flow"]),
        value_history=pd.DataFrame({"Close": [100., 120.]}, index=dates),
    )


class ValuationBasisTests(unittest.TestCase):
    def test_merging_providers_does_not_mix_statement_currencies(self):
        primary = example()
        fallback = example()
        primary.annual_income.attrs["financial_currency"] = "TWD"
        fallback.annual_income.attrs["financial_currency"] = "USD"
        fallback.annual_income.loc["EBITDA"] = 777
        merge_market_data(primary, fallback)
        self.assertNotIn("EBITDA", primary.annual_income.index)
        self.assertEqual(primary.diagnostics[-1]["kind"], "currency_mismatch")

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=10.)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_fx_and_adr_convert_history_but_not_issuer_market_cap_twice(self, rates, rate):
        data = example()
        rates.return_value = pd.Series([8., 10.], index=data.value_history.index)
        history = prepare_valuation_basis(data)
        context = build_historical_ratio_context(history, data.annual_income, data.annual_balance, data.annual_cashflow, years=1)

        self.assertEqual(history.Close.tolist(), [160., 240.])
        self.assertEqual(data.value_history.Close.tolist(), [100., 120.])
        self.assertEqual(data.info["currentPrice"], 100)
        self.assertEqual(context.statement_aligned_current_ratio("pe", data.info), 20)
        self.assertEqual(context.historical_ratios("pe"), [16., 24.])
        self.assertEqual(context.statement_aligned_current_ratio("ev_ebitda", data.info), None)
        self.assertEqual(fcf_yield(data.info, data.annual_cashflow), 2.)
        self.assertEqual(data.info["valuationBasis"]["ordinary_shares_per_receipt"], 5)

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=10.)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_missing_cap_is_reconstructed_from_ordinary_shares_divided_by_adr_ratio(self, rates, rate):
        data = example()
        data.info.pop("marketCap")
        rates.return_value = pd.Series(10., index=data.value_history.index)
        prepare_valuation_basis(data)
        self.assertEqual(data.info["marketCap"], 2000.)
        self.assertEqual(data.info["marketCapReporting"], 20000.)

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=None)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_missing_fx_cannot_fall_back_to_unconverted_price_times_shares(self, rates, rate):
        data = example()
        rates.return_value = pd.Series(float("nan"), index=data.value_history.index)
        history = prepare_valuation_basis(data)
        context = build_historical_ratio_context(history, data.annual_income, data.annual_balance, data.annual_cashflow, years=1)
        self.assertIsNone(context.statement_aligned_current_ratio("pe", data.info))
        self.assertEqual(context.historical_ratios("pe"), [])
        self.assertIsNone(fcf_yield(data.info, data.annual_cashflow))
        self.assertTrue(data.info["valuationNotes"])

    def test_unknown_or_conflicting_statement_currency_is_not_assumed_from_listing(self):
        data = example(quote="GBP", reporting="")
        prepare_valuation_basis(data)
        self.assertIsNone(data.info["marketCapReporting"])
        data.info["financialCurrency"] = "GBP"
        data.annual_income.attrs["financial_currency"] = "GEL"
        prepare_valuation_basis(data)
        self.assertIsNone(data.info["marketCapReporting"])

    def test_ordinary_share_and_verified_receipt_metadata(self):
        self.assertEqual(share_basis("PKN.WA", {})[0], 1.)
        self.assertEqual(share_basis("FUTU", {})[0], 8.)
        self.assertEqual(share_basis("BABA", {})[:2], (8., "2019-07-30"))
        self.assertEqual(share_basis("HTHT", {})[0], 10.)
        for ticker in ("SPOT", "ASML"):
            self.assertEqual(share_basis(ticker, {"country": "Netherlands"})[0], 1.)
        self.assertEqual(share_basis("OTHER", {"country": "Canada", "instrumentType": "ordinary_share", "instrumentTypeSource": "Issuer filing"})[0], 1.)
        self.assertIsNone(share_basis("OTHER", {"country": "China"})[0])
        self.assertEqual(share_basis("OTHER", {"ordinarySharesPerReceipt": 2, "shareRatioEffectiveFrom": "2020-01-01"})[0], 2.)

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=1.)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_baba_ratio_is_not_applied_before_documented_subdivision(self, rates, rate):
        data = example(ticker="BABA")
        data.value_history = pd.DataFrame({"Close": [80., 80.]}, index=pd.to_datetime(["2019-07-29", "2019-07-30"]))
        rates.return_value = pd.Series(1., index=data.value_history.index)
        history = prepare_valuation_basis(data)
        self.assertTrue(pd.isna(history.Close.iloc[0]))
        self.assertEqual(history.Close.iloc[1], 10.)

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=10.)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_unknown_adr_retains_reported_issuer_cap_but_disables_share_based_history(self, rates, rate):
        data = example(ticker="OTHER")
        data.info["country"] = "China"
        rates.return_value = pd.Series(10., index=data.value_history.index)
        history = prepare_valuation_basis(data)
        self.assertTrue(history.Close.isna().all())
        self.assertEqual(data.info["marketCapReporting"], 20000.)

    @patch("ticker_analyzer.analysis.valuation_basis.exchange_rate", return_value=10.)
    @patch("ticker_analyzer.analysis.valuation_basis.rates_on_dates")
    def test_engine_and_fair_value_keep_quote_currency_per_receipt(self, rates, rate):
        data = example()
        rates.return_value = pd.Series(10., index=data.value_history.index)
        # A reporting-currency EPS must not override the quote-currency P/E basis.
        data.info["trailingEps"] = 50
        engine = StockAnalysisEngine(provider=Mock(fetch=Mock(return_value=data)))
        result = engine.analyze("TSM", "1Y", load_config()).as_dict()
        inputs = inputs_from_analysis(result)
        self.assertEqual(inputs.current_price, 100.)
        self.assertEqual(inputs.currency, "USD")
        self.assertEqual(inputs.earnings_per_share, 5.)
        self.assertEqual(inputs.free_cash_flow_per_share, 2.)
        self.assertEqual(result["valuation_basis"]["reporting_currency"], "TWD")
