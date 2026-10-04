import unittest
from unittest.mock import Mock, PropertyMock, patch

import pandas as pd
from ticker_analyzer.domain import AnalysisRanges
from ticker_analyzer.providers.market_data import (
    YFinanceProvider,
    adjusted_price_history,
    clean_info_price,
    fill_missing_core_data,
    is_minor_gbp_currency,
    is_transient_provider_error,
    normalize_statement,
    safe_dict,
    safe_frame,
    safe_statement,
    statement_has_any_row,
    valuation_price_history,
)


class DataProviderTest(unittest.TestCase):
    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_value_history_uses_raw_close_and_growth_uses_adjusted_close(self, provider_class):
        from ticker_analyzer.providers.sec import empty_market_data

        provider_class.return_value.fetch.return_value = empty_market_data("ABC")
        class FakeTicker:
            info = {"symbol": "ABC"}
            financials = balance_sheet = cashflow = pd.DataFrame()
            quarterly_financials = quarterly_balance_sheet = quarterly_cashflow = pd.DataFrame()
            analyst_price_targets = {}
            revenue_estimate = earnings_estimate = eps_trend = growth_estimates = pd.DataFrame()

            def __init__(self):
                self.history_calls = []

            def history(self, **kwargs):
                self.history_calls.append(kwargs)
                return pd.DataFrame(
                    {"Close": [10.0], "Adj Close": [8.0]},
                    index=[pd.Timestamp("2026-01-01")],
                )

        fake = FakeTicker()
        with patch("ticker_analyzer.providers.market_data.yf.Ticker", return_value=fake):
            YFinanceProvider().fetch("ABC", AnalysisRanges.from_input("2Y"))
        self.assertEqual(len(fake.history_calls), 1)
        self.assertFalse(fake.history_calls[0]["auto_adjust"])
        self.assertTrue(fake.history_calls[0]["actions"])

    def test_adjusted_history_uses_adjusted_close_without_another_request(self):
        raw = pd.DataFrame({"Close": [10.0], "Adj Close": [8.0]})
        adjusted = adjusted_price_history(raw)
        self.assertEqual(adjusted["Close"].iloc[0], 8.0)
        self.assertEqual(raw["Close"].iloc[0], 10.0)

    def test_safe_frame_records_provider_failure(self):
        diagnostics = []

        def fail():
            raise RuntimeError("endpoint unavailable")

        result = safe_frame(fail, label="annual income statement", diagnostics=diagnostics)

        self.assertTrue(result.empty)
        self.assertEqual(diagnostics[0]["source"], "annual income statement")
        self.assertEqual(diagnostics[0]["kind"], "provider_error")
        self.assertIn("endpoint unavailable", diagnostics[0]["message"])

    def test_safe_dict_records_network_failure(self):
        diagnostics = []

        def fail():
            raise TimeoutError("request timed out")

        result = safe_dict(fail, label="company info", diagnostics=diagnostics)

        self.assertEqual(result, {})
        self.assertEqual(diagnostics[0]["kind"], "network_error")

    @patch("ticker_analyzer.providers.market_data.time.sleep")
    def test_safe_dict_retries_one_transient_failure(self, sleep):
        attempts = []

        def sometimes_fails():
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError("temporary timeout")
            return {"symbol": "ABC"}

        diagnostics = []
        result = safe_dict(sometimes_fails, label="company info", diagnostics=diagnostics)

        self.assertEqual(result, {"symbol": "ABC"})
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once()
        self.assertEqual(diagnostics, [])

    def test_safe_frame_does_not_report_valid_empty_data_as_failure(self):
        diagnostics = []

        result = safe_frame(lambda: pd.DataFrame(), label="earnings estimates", diagnostics=diagnostics)

        self.assertTrue(result.empty)
        self.assertEqual(diagnostics, [])

    def test_unauthorized_yahoo_response_is_transient(self):
        self.assertTrue(is_transient_provider_error(RuntimeError("HTTP Error 401: Unauthorized")))

    def test_clean_info_price_accepts_regular_market_fallback(self):
        self.assertEqual(clean_info_price({"regularMarketPrice": "12.5"}), 12.5)

    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_missing_core_data_is_filled_from_public_yahoo(self, provider_class):
        from ticker_analyzer.providers.sec import empty_market_data

        primary = empty_market_data("ABC", info={"symbol": "ABC", "industry": "Industrials"})
        fallback = empty_market_data(
            "ABC",
            info={"currentPrice": 12.0, "currency": "GBP", "sector": "Financial Services"},
            annual_income=pd.DataFrame({pd.Timestamp("2025-12-31"): [100.0]}, index=["Total Revenue"]),
            value_history=pd.DataFrame({"Close": [12.0]}, index=[pd.Timestamp("2026-01-01")]),
            diagnostics=[{"source": "batch provider", "kind": "fallback", "message": "public fallback"}],
        )
        provider_class.return_value.fetch.return_value = fallback

        fill_missing_core_data(primary, AnalysisRanges.from_input("2Y"))

        self.assertEqual(primary.info["currentPrice"], 12.0)
        self.assertEqual(primary.info["sector"], "Financial Services")
        self.assertFalse(primary.annual_income.empty)
        self.assertFalse(primary.value_history.empty)
        self.assertEqual(primary.diagnostics[-1]["kind"], "fallback")
        self.assertTrue(provider_class.call_args.kwargs["enrich_profile"])

    def test_nonempty_but_irrelevant_statement_is_not_treated_as_core_data(self):
        sparse = pd.DataFrame({pd.Timestamp("2025-12-31"): [1.0]}, index=["Tax Effect Of Unusual Items"])

        self.assertFalse(statement_has_any_row(sparse, frozenset({"Total Revenue"})))

    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_one_missing_statement_is_recovered_before_value_scoring(self, provider_class):
        from tests.test_analysis_engine import FakeProvider, market_data
        from ticker_analyzer.analysis.engine import StockAnalysisEngine
        from ticker_analyzer.config import load_config

        for statement in ("income", "balance", "cashflow"):
            with self.subTest(statement=statement):
                primary = market_data()
                setattr(primary, f"annual_{statement}", pd.DataFrame())
                setattr(primary, f"quarterly_{statement}", pd.DataFrame())
                before = StockAnalysisEngine(provider=FakeProvider(primary)).analyze("TEST", "2Y", load_config())
                self.assertIsNone(before.tabs["Value"]["score"])
                provider_class.return_value.fetch.return_value = market_data()

                fill_missing_core_data(primary, AnalysisRanges.from_input("2Y"))

                after = StockAnalysisEngine(provider=FakeProvider(primary)).analyze("TEST", "2Y", load_config())
                self.assertIsNotNone(after.tabs["Value"]["score"])
                self.assertFalse(getattr(primary, f"annual_{statement}").empty)

    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_missing_share_history_is_recovered_despite_populated_statements(self, provider_class):
        from tests.test_analysis_engine import market_data

        primary = market_data()
        primary.annual_balance = primary.annual_balance.drop(index="Ordinary Shares Number")
        primary.quarterly_balance = primary.quarterly_balance.drop(index="Ordinary Shares Number")
        provider_class.return_value.fetch.return_value = market_data()

        fill_missing_core_data(primary, AnalysisRanges.from_input("2Y"))

        self.assertIn("Ordinary Shares Number", primary.annual_balance.index)

    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_complete_data_does_not_trigger_extra_provider_requests(self, provider_class):
        from tests.test_analysis_engine import market_data

        fill_missing_core_data(market_data(), AnalysisRanges.from_input("2Y"))

        provider_class.assert_not_called()

    @patch("ticker_analyzer.ranking.provider.PublicYahooRankingProvider")
    def test_missing_reporting_currency_triggers_recovery_despite_complete_statements(self, provider_class):
        from tests.test_analysis_engine import FakeProvider, market_data
        from ticker_analyzer.analysis.engine import StockAnalysisEngine
        from ticker_analyzer.config import load_config

        primary = market_data()
        primary.info.pop("financialCurrency")
        fallback = market_data()
        fallback.info.pop("financialCurrency")
        for name in ("annual_income", "annual_balance", "annual_cashflow",
                     "quarterly_income", "quarterly_balance", "quarterly_cashflow"):
            getattr(fallback, name).attrs["financial_currency"] = "USD"
        provider_class.return_value.fetch.return_value = fallback

        fill_missing_core_data(primary, AnalysisRanges.from_input("2Y"))
        result = StockAnalysisEngine(provider=FakeProvider(primary)).analyze("NVDA", "2Y", load_config())

        provider_class.return_value.fetch.assert_called_once()
        self.assertEqual(result.valuation_basis["reporting_currency"], "USD")
        self.assertIsNotNone(result.tabs["Value"]["score"])

    def test_invalid_crumb_info_failure_recovers_value_from_public_statements(self):
        import requests
        from tests.test_analysis_engine import market_data
        from ticker_analyzer.analysis.engine import StockAnalysisEngine
        from ticker_analyzer.config import load_config
        from ticker_analyzer.ranking.provider import PublicYahooRankingProvider

        healthy = market_data()
        ticker = Mock()
        ticker_history = healthy.value_history.copy()
        ticker_history.index = ticker_history.index.tz_localize("America/New_York")
        ticker.history.return_value = ticker_history
        public_history = ticker_history.copy()
        public_history.index = public_history.index.tz_convert("UTC").tz_localize(None)
        fields = {
            "financials": "annual_income", "balance_sheet": "annual_balance", "cashflow": "annual_cashflow",
            "quarterly_financials": "quarterly_income", "quarterly_balance_sheet": "quarterly_balance",
            "quarterly_cashflow": "quarterly_cashflow",
        }
        statements = {}
        for prop, field in fields.items():
            setattr(ticker, prop, getattr(healthy, field))
            frame = getattr(healthy, field).copy()
            frame.attrs["financial_currency"] = "USD"
            statements[field.removeprefix("annual_")] = frame
        ticker.analyst_price_targets = {}
        ticker.revenue_estimate = ticker.earnings_estimate = ticker.eps_trend = ticker.growth_estimates = pd.DataFrame()
        # yfinance can either raise the 401 or hide it and return partial info.
        for info_response in (
            {"return_value": {"symbol": "NVDA"}},
            {"side_effect": requests.HTTPError("HTTP Error 401: Unauthorized, Invalid Crumb")},
        ):
            with (
                self.subTest(info_response=info_response),
                patch.object(type(ticker), "info", new_callable=PropertyMock, create=True, **info_response),
                patch("ticker_analyzer.providers.market_data.yf.Ticker", return_value=ticker),
                patch.object(PublicYahooRankingProvider, "_profile", return_value={"industry": "Semiconductors"}),
                patch.object(PublicYahooRankingProvider, "_statements", return_value=statements),
                patch.object(PublicYahooRankingProvider, "_history", return_value=(
                    public_history, public_history, {"currency": "USD", "regularMarketPrice": 150.}
                )),
            ):
                result = StockAnalysisEngine(provider=YFinanceProvider()).analyze("NVDA", "2Y", load_config())

            self.assertEqual(result.valuation_basis["reporting_currency"], "USD")
            self.assertIsNotNone(result.tabs["Value"]["score"])
            self.assertFalse(any(
                item.get("source") == "public Yahoo core-data fallback" and item.get("kind") == "provider_error"
                for item in result.diagnostics
            ))
            self.assertTrue(all(
                result.raw[name]["value"] is not None
                for name in ("pe_current", "price_to_sales_current", "ev_ebitda_current", "fcf_yield_ttm")
            ))

    def test_core_statement_rows_without_numeric_values_are_missing(self):
        for value in (None, float("nan"), "unavailable"):
            with self.subTest(value=value):
                sparse = pd.DataFrame({pd.Timestamp("2025-12-31"): [value]}, index=["Total Revenue"])
                self.assertFalse(statement_has_any_row(sparse, frozenset({"Total Revenue"})))
        zero = pd.DataFrame({pd.Timestamp("2025-12-31"): [0.]}, index=["Total Revenue"])
        self.assertTrue(statement_has_any_row(zero, frozenset({"Total Revenue"})))

    def test_london_pence_history_is_converted_for_valuation_only(self):
        history = pd.DataFrame({"Close": [13_620.0], "Adj Close": [13_500.0]})

        normalized = valuation_price_history(history, "GBp")

        self.assertEqual(normalized["Close"].iloc[0], 136.2)
        self.assertEqual(normalized["Adj Close"].iloc[0], 135.0)
        self.assertEqual(history["Close"].iloc[0], 13_620.0)
        self.assertTrue(is_minor_gbp_currency("GBX"))
        self.assertFalse(is_minor_gbp_currency("GBP"))

    def test_normalize_statement_preserves_callers_frame_by_default(self):
        original = pd.DataFrame({"2025-12-31": [1], "2024-12-31": [2]})

        normalized = normalize_statement(original)

        self.assertEqual(list(original.columns), ["2025-12-31", "2024-12-31"])
        self.assertEqual(
            list(normalized.columns),
            [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-12-31")],
        )

    def test_safe_statement_returns_an_independent_normalized_frame(self):
        original = pd.DataFrame({"2025-12-31": [1], "2024-12-31": [2]})

        normalized = safe_statement(lambda: original)

        self.assertIsNot(normalized, original)
        self.assertEqual(list(original.columns), ["2025-12-31", "2024-12-31"])
        self.assertEqual(
            list(normalized.columns),
            [pd.Timestamp("2024-12-31"), pd.Timestamp("2025-12-31")],
        )


if __name__ == "__main__":
    unittest.main()
