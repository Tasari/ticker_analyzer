import unittest
from unittest.mock import patch

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
    def test_value_history_uses_raw_close_and_growth_uses_adjusted_close(self):
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
