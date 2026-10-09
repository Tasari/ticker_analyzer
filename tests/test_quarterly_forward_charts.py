from __future__ import annotations

import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest
from tests.test_analysis_engine import market_data, statement
from ticker_analyzer.metrics.financial_trends import build_financial_trends, build_ps_trends
from ticker_analyzer.ui.analysis_views import ps_trends_figure


class QuarterlyFinancialTrendsTest(unittest.TestCase):
    def setUp(self):
        self.data = market_data()
        self.dates = pd.date_range("2023-03-31", periods=12, freq="QE")
        revenue = [100, 200, 300, 400, 120, 240, 360, 480, 144, 288, 432, 576]
        self.income = statement({"Total Revenue": revenue, "Net Income": [value * .1 for value in revenue]}, self.dates)
        self.cashflow = statement({"Operating Cash Flow": [value * .2 for value in revenue]}, self.dates)

    def charts(self):
        return build_financial_trends(
            self.data.annual_income, self.data.annual_cashflow,
            quarterly_income=self.income, quarterly_cashflow=self.cashflow, info={"financialCurrency": "USD"},
        )

    def test_twelve_quarterly_points_with_forward_values_on_the_same_dates(self):
        actual, forward, _ = self.charts()
        self.assertEqual(len(actual), 12)
        self.assertTrue(actual.index.equals(forward.index))
        self.assertEqual(actual.iloc[0]["Revenue"], 100)
        self.assertEqual(actual.iloc[-1]["Revenue"], 576)
        self.assertAlmostEqual(forward.iloc[4]["Revenue"], 172.8, delta=.1)
        self.assertAlmostEqual(forward.iloc[-1]["Net Income"], forward.iloc[-1]["Revenue"] * .1)
        self.assertAlmostEqual(forward.iloc[-1]["Operating Cash Flow"], forward.iloc[-1]["Revenue"] * .2)
        self.assertTrue(forward.iloc[:4].isna().all().all())

    def test_later_financials_do_not_rewrite_earlier_forecasts(self):
        before = self.charts()[1].iloc[4:8].copy()
        self.income.loc[:, self.dates[-4:]] *= 100
        self.cashflow.loc[:, self.dates[-4:]] *= 100
        pd.testing.assert_frame_equal(before, self.charts()[1].iloc[4:8])

    def test_later_filing_is_excluded_from_historical_margins(self):
        self.cashflow.attrs["filed_dates"] = {date: pd.Timestamp("2027-01-01") for date in self.dates}
        self.assertTrue(self.charts()[1]["Operating Cash Flow"].isna().all())

    def test_missing_quarter_is_not_interpolated(self):
        self.income = self.income.drop(columns=self.dates[5])
        actual, forward, _ = self.charts()
        self.assertEqual(len(actual), 12)
        self.assertTrue(pd.isna(actual.iloc[5]["Revenue"]))
        self.assertTrue(pd.isna(forward.iloc[5]["Revenue"]))

    def test_cashflow_starting_later_is_aligned_to_income_quarters(self):
        self.cashflow = self.cashflow.iloc[:, 1:]
        actual = self.charts()[0]
        self.assertEqual(len(actual), 12)
        self.assertTrue(pd.isna(actual.iloc[0]["Operating Cash Flow"]))
        self.assertEqual(actual.iloc[-1]["Operating Cash Flow"], 115.2)

    def test_missing_cashflow_does_not_block_revenue_and_income(self):
        self.cashflow = pd.DataFrame()
        actual, forward, _ = self.charts()
        self.assertEqual(len(actual), 12)
        self.assertTrue(actual["Operating Cash Flow"].isna().all())
        self.assertTrue(forward["Operating Cash Flow"].isna().all())
        self.assertTrue(forward.iloc[-1][["Revenue", "Net Income"]].notna().all())

    def test_annual_only_and_semiannual_data_do_not_become_fake_quarters(self):
        history, _, _ = build_financial_trends(self.data.annual_income, self.data.annual_cashflow)
        self.assertTrue(history.empty)
        self.income = self.income.iloc[:, ::2]
        self.cashflow = self.cashflow.iloc[:, ::2]
        self.assertTrue(self.charts()[0].empty)

    def test_saved_annual_result_asks_for_refresh(self):
        app = AppTest.from_string('''
import pandas as pd
from ticker_analyzer.ui.analysis_views import render_financial_trends
render_financial_trends({"financials": pd.DataFrame({"Revenue": [100]}, index=pd.to_datetime(["2025-12-31"]))})
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("click Analyze" in item.value for item in app.info))
        self.assertFalse(app.get("plotly_chart"))


class PsTrendsTest(unittest.TestCase):
    def setUp(self):
        self.data = market_data()
        dates = pd.date_range("2022-03-31", periods=16, freq="QE")
        revenue = [100] * 4 + [110] * 4 + [121] * 4 + [133.1] * 4
        self.data.quarterly_income = statement({"Total Revenue": revenue}, dates)
        self.data.quarterly_balance = statement({"Ordinary Shares Number": [10] * 16}, dates)
        self.data.value_history = pd.DataFrame({"Close": 100.}, index=pd.date_range("2022-01-01", "2026-03-31", freq="B"))
        self.data.info = {"marketCap": 1000, "currency": "USD", "financialCurrency": "USD"}
        self.data.revenue_estimate = pd.DataFrame()
        self.as_of = pd.Timestamp("2026-03-31")

    def charts(self):
        data = self.data
        ratios, sources = build_ps_trends(
            data.annual_income, data.annual_cashflow, data.annual_balance, data.value_history,
            quarterly_income=data.quarterly_income, quarterly_balance=data.quarterly_balance,
            quarterly_cashflow=data.quarterly_cashflow, revenue_estimate=data.revenue_estimate,
            info=data.info, as_of=self.as_of,
        )
        return {"ps_ratios": ratios, "ps_sources": sources}

    def test_current_and_forward_use_same_capitalization_and_observation_dates(self):
        charts = self.charts()
        current = charts["ps_ratios"].iloc[-1]
        self.assertAlmostEqual(current["P/S TTM"], 1000 / 532.4)
        self.assertAlmostEqual(current["P/S +2Y"], 1000 / (532.4 * 1.1 ** 2), delta=.002)
        figure = ps_trends_figure(charts)
        self.assertEqual(list(figure.data[0].x), list(figure.data[1].x))
        self.assertEqual(figure.data[1].line.dash, "dash")
        self.assertEqual(figure.data[1].customdata[-1][0], "2028-03-31")
        self.assertEqual(figure.data[0].mode, "lines")
        self.assertEqual(figure.data[1].mode, "lines")
        self.assertEqual(figure.layout.xaxis.title.text, "Date")

    def test_daily_prices_drive_both_lines_between_quarterly_reports(self):
        first, second = pd.Timestamp("2025-05-12"), pd.Timestamp("2025-05-13")
        self.data.value_history.loc[second, "Close"] = 200
        ratios = self.charts()["ps_ratios"]
        self.assertGreater(len(ratios), 700)
        self.assertAlmostEqual(ratios.loc[second, "P/S TTM"], ratios.loc[first, "P/S TTM"] * 2)
        self.assertAlmostEqual(ratios.loc[second, "P/S +2Y"], ratios.loc[first, "P/S +2Y"] * 2)
        self.assertNotIn(pd.Timestamp("2025-05-11"), ratios.index)

    def test_today_consensus_does_not_rewrite_historical_ps(self):
        self.as_of = pd.Timestamp("2025-12-31")
        before = self.charts()["ps_ratios"].copy()
        self.data.info["nextFiscalYearEnd"] = pd.Timestamp("2026-12-31").timestamp()
        self.data.revenue_estimate = pd.DataFrame({"avg": [600, 800], "currency": ["USD", "USD"]}, index=["0y", "+1y"])
        charts = self.charts()
        after = charts["ps_ratios"]
        pd.testing.assert_frame_equal(before.iloc[:-1], after.iloc[:-1])
        self.assertEqual(after.iloc[-1]["P/S +2Y"], 1.25)
        self.assertIn("Current analyst", charts["ps_sources"].iloc[-1]["P/S +2Y"])

    def test_consensus_must_match_two_year_target_date_and_currency(self):
        self.data.info["nextFiscalYearEnd"] = pd.Timestamp("2026-12-31").timestamp()
        self.data.revenue_estimate = pd.DataFrame({"avg": [600, 800], "currency": ["USD", "USD"]}, index=["0y", "+1y"])
        self.assertIn("Model", self.charts()["ps_sources"].iloc[-1]["P/S +2Y"])
        self.as_of = pd.Timestamp("2025-12-31")
        self.data.revenue_estimate["currency"] = "EUR"
        self.assertIn("Model", self.charts()["ps_sources"].iloc[-1]["P/S +2Y"])

    def test_historical_denominator_uses_only_published_quarters(self):
        self.data.quarterly_income.attrs["filed_dates"] = {
            date: date + pd.Timedelta(days=365) for date in self.data.quarterly_income.columns
        }
        before = self.charts()["ps_ratios"].copy()
        self.data.quarterly_income.loc[:, pd.Timestamp("2025-12-31")] = 999999
        pd.testing.assert_frame_equal(before.iloc[:-1], self.charts()["ps_ratios"].iloc[:-1])

    def test_missing_fx_or_unverified_share_basis_does_not_create_ps(self):
        self.data.info.update(valuationBasisPrepared=True, marketCapReporting=None)
        self.data.value_history["Close"] = float("nan")
        self.assertTrue(self.charts()["ps_ratios"].isna().all().all())

    def test_zero_revenue_does_not_create_an_infinite_multiple(self):
        self.data.annual_income.loc["Total Revenue"] = 0
        self.data.quarterly_income.loc["Total Revenue"] = 0
        self.assertTrue(self.charts()["ps_ratios"].isna().all().all())


if __name__ == "__main__":
    unittest.main()
