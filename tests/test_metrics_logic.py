import unittest

import pandas as pd
from ticker_analyzer.config import ConfigValidationError, normalize_config
from ticker_analyzer.domain import AnalysisRanges
from ticker_analyzer.engine import (
    build_historical_ratio_context,
    cagr_pct,
    cfo_to_debt,
    company_profile,
    config_for_profile,
    estimate_growth,
    fcf_margin_observations,
    fcf_yield,
    gross_margin_trend,
    momentum_12_1,
    net_debt_to_ebitda_observations,
    overall_score_with_missing_policy,
    range_ratio_metric,
    roic_observations,
    share_count_cagr,
    statement_aligned_ratio_vs_history_metric,
    statement_ratio_median,
    ttm_range_cagr,
)
from ticker_analyzer.metrics.formulas import series_coefficient_of_variation
from ticker_analyzer.scoring import classify_tab_rating


class MetricsLogicTest(unittest.TestCase):
    def test_stability_rejects_negative_and_zero_crossing_series(self):
        self.assertIsNone(series_coefficient_of_variation(pd.Series([-3.0, -2.0, -1.0])))
        self.assertIsNone(series_coefficient_of_variation(pd.Series([-1.0, 1.0, 2.0])))
        self.assertIsNotNone(series_coefficient_of_variation(pd.Series([1.0, 1.1, 1.2])))

    def test_cagr_pct_annualizes_multi_year_growth(self):
        self.assertAlmostEqual(cagr_pct(121, 100, 2), 10.0)

    def test_cagr_pct_returns_none_for_non_positive_base(self):
        self.assertIsNone(cagr_pct(100, 0, 3))
        self.assertIsNone(cagr_pct(-100, 100, 3))

    def test_momentum_12_1_skips_latest_month(self):
        index = pd.date_range("2024-01-31", periods=14, freq="ME")
        history = pd.DataFrame({"Close": range(100, 114)}, index=index)
        expected = (112 - 101) / 101 * 100
        self.assertAlmostEqual(momentum_12_1(history), expected)

    def test_momentum_12_1_is_missing_when_history_is_too_short(self):
        index = pd.date_range("2025-01-31", periods=12, freq="ME")
        history = pd.DataFrame({"Close": range(100, 112)}, index=index)
        self.assertIsNone(momentum_12_1(history))

    def test_cfo_to_debt_rewards_debt_free_positive_cash_flow(self):
        cashflow = pd.DataFrame({pd.Timestamp("2025-12-31"): [100]}, index=["Operating Cash Flow"])
        balance = pd.DataFrame({pd.Timestamp("2025-12-31"): [0]}, index=["Total Debt"])
        self.assertEqual(cfo_to_debt(cashflow, balance), 10.0)

    def test_ttm_range_cagr_uses_quarterly_ttm_windows(self):
        dates = pd.date_range("2024-03-31", periods=8, freq="QE")
        values = [25, 25, 25, 25, 30.25, 30.25, 30.25, 30.25]
        frame = pd.DataFrame({date: [value] for date, value in zip(dates, values, strict=True)}, index=["Total Revenue"])
        result, note = ttm_range_cagr(frame, ["Total Revenue"], 1)
        self.assertAlmostEqual(result, 21.0)
        self.assertIn("TTM vs TTM", note)

    def test_statement_ratio_median_changes_with_selected_range(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        numerator = pd.DataFrame({date: [value] for date, value in zip(dates, [10, 30, 90], strict=True)}, index=["Debt"])
        denominator = pd.DataFrame({date: [100] for date in dates}, index=["Assets"])
        self.assertEqual(statement_ratio_median(numerator, ["Debt"], denominator, ["Assets"], 1, multiplier=100), 90)
        self.assertEqual(statement_ratio_median(numerator, ["Debt"], denominator, ["Assets"], 3, multiplier=100), 30)

    def test_multi_year_range_metric_requires_two_observations(self):
        result = range_ratio_metric([25.0], 4)
        self.assertIsNone(result["value"])
        self.assertIn("1 available annual observation", result["note"])
        self.assertIn("requires at least 2", result["note"])

    def test_default_analysis_range_is_two_years(self):
        ranges = AnalysisRanges.from_input({})
        self.assertEqual(ranges.as_dict(), {"Growth": "2Y", "Fundamentals": "2Y", "Value": "2Y"})

    def test_analysis_ranges_carry_data_as_of_without_exposing_it_as_a_tab(self):
        ranges = AnalysisRanges.from_input(
            {"Growth": "3Y", "Fundamentals": "2Y", "Value": "1Y", "_data_as_of": "2026-07-31"}
        )
        self.assertEqual(ranges.data_as_of.date().isoformat(), "2026-07-31")
        self.assertEqual(set(ranges.as_dict()), {"Growth", "Fundamentals", "Value"})

    def test_fcf_yield_uses_free_cash_flow(self):
        cashflow = pd.DataFrame({pd.Timestamp("2025-12-31"): [50]}, index=["Free Cash Flow"])
        self.assertEqual(fcf_yield({"marketCap": 1000}, cashflow), 5.0)

    def test_share_count_cagr_detects_dilution(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        balance = pd.DataFrame({date: [value] for date, value in zip(dates, [100, 110, 121], strict=True)}, index=["Ordinary Shares Number"])
        self.assertAlmostEqual(share_count_cagr(balance, 2), 10.0)

    def test_gross_margin_trend_returns_percentage_point_change(self):
        dates = pd.date_range("2024-12-31", periods=2, freq="YE")
        income = pd.DataFrame(
            {
                dates[0]: [40, 100],
                dates[1]: [50, 100],
            },
            index=["Gross Profit", "Total Revenue"],
        )
        self.assertAlmostEqual(gross_margin_trend(income, 1), 10.0)

    def test_roic_observations_use_nopat_and_invested_capital(self):
        date = pd.Timestamp("2025-12-31")
        income = pd.DataFrame({date: [100, 0.2]}, index=["EBIT", "Tax Rate For Calcs"])
        balance = pd.DataFrame({date: [400]}, index=["Invested Capital"])
        self.assertEqual(roic_observations(income, balance, 1), [20.0])

    def test_roic_observations_accept_percentage_tax_rate(self):
        date = pd.Timestamp("2025-12-31")
        income = pd.DataFrame({date: [100, 20]}, index=["EBIT", "Tax Rate For Calcs"])
        balance = pd.DataFrame({date: [400]}, index=["Invested Capital"])
        self.assertEqual(roic_observations(income, balance, 1), [20.0])

    def test_fcf_margin_and_net_debt_to_ebitda_observations(self):
        date = pd.Timestamp("2025-12-31")
        income = pd.DataFrame({date: [200, 50]}, index=["Total Revenue", "EBITDA"])
        cashflow = pd.DataFrame({date: [20]}, index=["Free Cash Flow"])
        balance = pd.DataFrame({date: [100]}, index=["Net Debt"])
        self.assertEqual(fcf_margin_observations(income, cashflow, 1), [10.0])
        self.assertEqual(net_debt_to_ebitda_observations(income, balance, 1), [2.0])

    def test_value_ratio_uses_statement_aligned_current_before_feed_fallback(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        history = pd.DataFrame({"Close": [10, 20, 30]}, index=dates)
        income = pd.DataFrame({date: [revenue] for date, revenue in zip(dates, [100, 200, 300], strict=True)}, index=["Total Revenue"])
        balance = pd.DataFrame(
            {date: [10] for date in dates},
            index=["Ordinary Shares Number"],
        )
        cashflow = pd.DataFrame()
        context = build_historical_ratio_context(history, income, balance, cashflow, years=3)

        result = statement_aligned_ratio_vs_history_metric(
            {"marketCap": 450, "priceToSalesTrailing12Months": 99},
            "ps",
            context,
            fallback_current_ratio=99,
        )

        # 2024/2025 prices may only use statements known after a 90-day filing
        # lag, so the comparison does not leak same-period year-end facts.
        self.assertAlmostEqual(result["value"], -14.285714285714285)
        self.assertIn("statement-aligned current multiple", result["note"])

    def test_ev_ebitda_history_uses_debt_and_cash(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        history = pd.DataFrame({"Close": [10, 20, 30]}, index=dates)
        income = pd.DataFrame({date: [ebitda] for date, ebitda in zip(dates, [50, 100, 120], strict=True)}, index=["EBITDA"])
        balance = pd.DataFrame(
            {
                dates[0]: [10, 30, 5],
                dates[1]: [10, 40, 10],
                dates[2]: [10, 50, 20],
            },
            index=["Ordinary Shares Number", "Total Debt", "Cash And Cash Equivalents"],
        )
        context = build_historical_ratio_context(history, income, balance, pd.DataFrame(), years=3)

        result = statement_aligned_ratio_vs_history_metric(
            {"marketCap": 450},
            "ev_ebitda",
            context,
        )

        self.assertAlmostEqual(result["value"], 2.5641025641025665)

    def test_historical_pe_skips_negative_earnings_observations(self):
        dates = pd.date_range("2023-12-31", periods=3, freq="YE")
        history = pd.DataFrame({"Close": [10, 20, 30]}, index=dates)
        income = pd.DataFrame({date: [ni] for date, ni in zip(dates, [-10, 100, 150], strict=True)}, index=["Net Income"])
        balance = pd.DataFrame({date: [10] for date in dates}, index=["Ordinary Shares Number"])
        context = build_historical_ratio_context(history, income, balance, pd.DataFrame(), years=3)

        self.assertEqual(context.historical_ratios("pe"), [3.0])

    def test_historical_multiple_is_split_invariant_with_raw_prices_and_period_shares(self):
        dates = pd.to_datetime(["2026-01-31", "2026-02-28"])
        history = pd.DataFrame({"Close": [100.0, 50.0]}, index=dates)
        income = pd.DataFrame({dates[0]: [10.0]}, index=["Net Income"])
        balance = pd.DataFrame(
            {dates[0]: [1.0], dates[1]: [2.0]}, index=["Ordinary Shares Number"]
        )
        income.attrs["filed_dates"] = {dates[0]: dates[0]}
        balance.attrs["filed_dates"] = {date: date for date in dates}
        context = build_historical_ratio_context(history, income, balance, pd.DataFrame(), years=1)
        self.assertEqual(context.historical_ratios("pe"), [10.0, 10.0])

    def test_tab_rating_uses_configured_tab_thresholds(self):
        config = {
            "rating_thresholds": {"very_strong": 90, "strong": 70, "neutral": 50, "weak": 30},
            "tab_rating_thresholds": {"Value": {"very_strong": 85}},
            "tab_rating_labels": {
                "Value": {
                    "very_strong": "Very Underpriced",
                    "strong": "Underpriced",
                    "neutral": "Fair",
                    "weak": "Overpriced",
                    "very_weak": "Very Overpriced",
                }
            },
        }
        self.assertEqual(classify_tab_rating("Value", 86, config), "Very Underpriced")
        self.assertEqual(classify_tab_rating("Value", 80, config), "Underpriced")

    def test_estimate_growth_prefers_structured_table(self):
        table = pd.DataFrame(
            {
                "avg": [100.0, 115.0],
                "numberOfAnalysts": [8, 9],
            },
            index=["0y", "+1y"],
        )
        self.assertAlmostEqual(estimate_growth({}, "revenue", table), 15.0)

    def test_estimate_growth_rejects_low_analyst_count(self):
        table = pd.DataFrame(
            {
                "avg": [100.0, 150.0],
                "numberOfAnalysts": [2, 2],
            },
            index=["0y", "+1y"],
        )
        self.assertIsNone(estimate_growth({}, "revenue", table))

    def test_eps_estimate_growth_treats_transition_through_zero_as_turnaround(self):
        table = pd.DataFrame(
            {
                "avg": [-1.0, 1.0],
                "numberOfAnalysts": [8, 9],
            },
            index=["0y", "+1y"],
        )
        self.assertIsNone(estimate_growth({}, "eps", table))

    def test_eps_turnaround_does_not_fall_back_to_generic_growth_estimate(self):
        growth_estimates = pd.DataFrame({"stockTrend": [0.5]}, index=["+1y"])
        info = {"epsCurrentYear": -1.0, "epsNextYear": 1.0}
        self.assertIsNone(estimate_growth(info, "eps", growth_estimates=growth_estimates))

    def test_normalize_config_rejects_missing_metrics(self):
        with self.assertRaises(ConfigValidationError):
            normalize_config({"tab_weights": {}, "rating_thresholds": {}})

    def test_overall_score_can_use_partial_tabs_when_configured(self):
        tab_results = {
            "Growth": {"score": 80},
            "Fundamentals": {"score": None},
            "Value": {"score": 60},
        }
        config = {
            "tab_weights": {"Growth": 1, "Fundamentals": 1, "Value": 1},
            "missing_policy": {"require_all_tabs_for_overall": False, "minimum_scored_tabs": 2},
        }
        self.assertAlmostEqual(overall_score_with_missing_policy(tab_results, config), 70.0)

    def test_overall_score_can_require_all_tabs(self):
        tab_results = {
            "Growth": {"score": 80},
            "Fundamentals": {"score": None},
            "Value": {"score": 60},
        }
        config = {
            "tab_weights": {"Growth": 1, "Fundamentals": 1, "Value": 1},
            "missing_policy": {"require_all_tabs_for_overall": True, "minimum_scored_tabs": 2},
        }
        self.assertIsNone(overall_score_with_missing_policy(tab_results, config))

    def test_company_profile_detects_financial_industries(self):
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Banks - Diversified"}), "FinancialBank")
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Credit Services"}), "FinancialLender")
        self.assertEqual(company_profile({"quoteType": "EQUITY", "industry": "Consumer Electronics"}), "Industrial")
        self.assertEqual(company_profile({}, official_ids={"fdic_cert": "3510"}), "FinancialBank")
        self.assertEqual(company_profile({}, official_ids={"sic": "6211"}), "FinancialBroker")
        self.assertEqual(company_profile({}, official_ids={"sic": "6311"}), "FinancialInsurance")

    def test_config_for_profile_uses_financial_metric_override(self):
        config = {
            "metrics": {"Growth": [{"id": "industrial"}]},
            "profile_metrics": {"Financial": {"Growth": [{"id": "financial"}]}},
        }
        self.assertEqual(config_for_profile(config, "Financial")["metrics"]["Growth"][0]["id"], "financial")
        self.assertEqual(config_for_profile(config, "Industrial")["metrics"]["Growth"][0]["id"], "industrial")


if __name__ == "__main__":
    unittest.main()
