from __future__ import annotations

import json
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest
from tests.test_analysis_engine import market_data
from ticker_analyzer.metrics.financial_trends import _annual_financial_trends, build_financial_trends
from ticker_analyzer.ui.analysis_views import financial_trends_figure


class AnnualForecastInputsTest(unittest.TestCase):
    def setUp(self):
        dates = pd.to_datetime(["2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30"])
        self.income = pd.DataFrame(
            [[50, 100, 120, 144], [-10, -10, 12, 28.8]],
            index=["Total Revenue", "Net Income"], columns=dates,
        )
        self.cashflow = pd.DataFrame([[10, 20, 24, 28.8]], index=["Operating Cash Flow"], columns=dates)
        self.info = {"financialCurrency": "USD", "nextFiscalYearEnd": pd.Timestamp("2026-06-30").timestamp()}

    def charts(self, estimates=None, info=None):
        history, future, sources = _annual_financial_trends(
            self.income, self.cashflow,
            revenue_estimate=estimates, info=self.info if info is None else info,
        )
        return {"financials": history, "financial_estimates": future, "financial_estimate_sources": sources}

    def test_three_reported_years_and_two_consensus_years_with_model_margins(self):
        estimates = pd.DataFrame({"avg": [175, 210], "currency": ["USD", "USD"]}, index=["0y", "+1y"])
        charts = self.charts(estimates)
        history, future = charts["financials"], charts["financial_estimates"]
        self.assertEqual(list(history.index.year), [2023, 2024, 2025])
        self.assertEqual(list(future.index.year), [2026, 2027])
        self.assertEqual(list(future.index.month), [6, 6])
        self.assertEqual(list(future["Revenue"]), [175, 210])
        self.assertEqual(list(future["Net Income"]), [17.5, 21])
        self.assertEqual(list(future["Operating Cash Flow"]), [35, 42])
        self.assertIn("Analyst", charts["financial_estimate_sources"].iloc[0]["Revenue"])
        self.assertIn("Model", charts["financial_estimate_sources"].iloc[0]["Net Income"])
        self.assertEqual(len(self.income.columns), 4)

    def test_missing_consensus_uses_historical_cagr(self):
        future = self.charts()["financial_estimates"]
        self.assertAlmostEqual(future.iloc[0]["Revenue"], 172.8)
        self.assertAlmostEqual(future.iloc[1]["Revenue"], 207.36)

    def test_consensus_currency_mismatch_uses_model(self):
        estimates = pd.DataFrame({"avg": [999, 999], "currency": ["EUR", "EUR"]}, index=["0y", "+1y"])
        charts = self.charts(estimates)
        self.assertAlmostEqual(charts["financial_estimates"].iloc[0]["Revenue"], 172.8)
        self.assertIn("CAGR", charts["financial_estimate_sources"].iloc[0]["Revenue"])

    def test_consensus_is_assigned_to_its_fiscal_year_when_history_is_stale(self):
        estimates = pd.DataFrame({"avg": [500, 600], "currency": ["USD", "USD"]}, index=["0y", "+1y"])
        charts = self.charts(estimates, {**self.info, "nextFiscalYearEnd": pd.Timestamp("2027-06-30").timestamp()})
        future = charts["financial_estimates"]
        self.assertAlmostEqual(future.iloc[0]["Revenue"], 172.8)
        self.assertEqual(future.iloc[1]["Revenue"], 500)

    def test_missing_historical_year_remains_a_gap_and_cagr_uses_elapsed_years(self):
        self.income = self.income.drop(columns=pd.Timestamp("2024-06-30"))
        charts = self.charts()
        self.assertTrue(pd.isna(charts["financials"].loc["2024-06-30", "Revenue"]))
        self.assertAlmostEqual(charts["financial_estimates"].iloc[0]["Revenue"], 172.8)

    def test_week_based_fiscal_dates_and_descending_columns_are_aligned(self):
        self.income.columns = pd.to_datetime(["2022-07-02", "2023-07-01", "2024-06-29", "2025-06-28"])
        self.cashflow.columns = self.income.columns
        self.income = self.income.iloc[:, ::-1]
        charts = self.charts()
        self.assertEqual(list(charts["financials"]["Revenue"]), [100, 120, 144])
        self.assertEqual(list(charts["financials"]["Operating Cash Flow"]), [20, 24, 28.8])

    def test_insufficient_history_does_not_invent_forecasts(self):
        self.income = self.income.iloc[:, -1:]
        self.cashflow = pd.DataFrame()
        charts = self.charts()
        self.assertTrue(charts["financial_estimates"].isna().all().all())
        self.assertTrue(charts["financials"].iloc[:2].isna().all().all())

    def test_absent_financials_return_empty_charts(self):
        self.income = self.cashflow = pd.DataFrame()
        charts = self.charts()
        self.assertTrue(charts["financials"].empty)
        self.assertTrue(charts["financial_estimates"].empty)

    def test_estimate_lines_overlay_quarters_with_matching_colors(self):
        data = market_data()
        history, forward, sources = build_financial_trends(
            data.annual_income, data.annual_cashflow, quarterly_income=data.quarterly_income,
            quarterly_cashflow=data.quarterly_cashflow, info={"financialCurrency": "USD"},
        )
        charts = {"financials": history, "financial_estimates": forward, "financial_estimate_sources": sources}
        figure = financial_trends_figure(charts)
        self.assertEqual(figure.layout.title.text, "Financial Trends")
        self.assertEqual(figure.layout.yaxis.title.text, "Quarterly value (USD)")
        self.assertEqual(len(figure.data), 6)
        for actual, estimate in zip(figure.data[::2], figure.data[1::2], strict=True):
            self.assertEqual(actual.line.dash, "solid")
            self.assertEqual(estimate.line.dash, "dash")
            self.assertEqual(actual.line.color, estimate.line.color)
            self.assertEqual(len(actual.x), 12)
            self.assertEqual(list(actual.x), list(estimate.x))
            self.assertEqual(estimate.customdata[0][0], "2025-03-31")
            self.assertIn("Model", estimate.customdata[-1][1])

    def test_all_company_charts_are_in_first_graphs_tab_and_fair_value_stays_editable(self):
        app = AppTest.from_string('''
from tests.test_analysis_engine import FakeProvider, market_data
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config
from ticker_analyzer.ui.analysis_views import render_tabs
result = StockAnalysisEngine(provider=FakeProvider(market_data())).analyze("TEST", "2Y", load_config()).as_dict()
render_tabs(result)
''').run()
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], ["Graphs", "Growth", "Fundamentals", "Value", "Fair Value"])
        self.assertEqual(len(app.tabs[0].get("plotly_chart")), 5)
        for tab in app.tabs[1:]:
            self.assertEqual(len(tab.get("plotly_chart")), 0)
        fair_chart = app.tabs[0].get("plotly_chart")[-1]
        before = json.loads(fair_chart.proto.spec)
        app.number_input[0].set_value(10).run()
        self.assertFalse(app.exception)
        after = json.loads(app.tabs[0].get("plotly_chart")[-1].proto.spec)
        self.assertNotEqual(before["data"], after["data"])


if __name__ == "__main__":
    unittest.main()
