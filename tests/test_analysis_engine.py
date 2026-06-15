import unittest

import pandas as pd

from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config, normalize_config
from ticker_analyzer.domain import AnalysisRanges, MarketData


def statement(rows: dict[str, list[float]], dates: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {date: [values[index] for values in rows.values()] for index, date in enumerate(dates)},
        index=rows.keys(),
    )


def market_data(*, industry: str = "Software - Infrastructure") -> MarketData:
    annual_dates = pd.date_range("2023-12-31", periods=3, freq="YE")
    quarter_dates = pd.date_range("2023-03-31", periods=12, freq="QE")
    price_dates = pd.date_range("2023-01-31", periods=36, freq="ME")
    annual_income = statement(
        {
            "Total Revenue": [800, 900, 1000],
            "Net Income": [80, 95, 110],
            "Operating Income": [120, 145, 170],
            "Gross Profit": [400, 470, 550],
            "EBIT": [120, 145, 170],
            "EBITDA": [150, 180, 210],
            "Interest Expense": [20, 18, 16],
            "Tax Rate For Calcs": [0.2, 0.2, 0.2],
        },
        annual_dates,
    )
    annual_balance = statement(
        {
            "Total Assets": [1000, 1100, 1200],
            "Total Debt": [300, 280, 250],
            "Current Assets": [400, 450, 500],
            "Current Liabilities": [250, 260, 270],
            "Cash Cash Equivalents And Short Term Investments": [100, 130, 160],
            "Receivables": [120, 135, 150],
            "Stockholders Equity": [500, 590, 680],
            "Invested Capital": [700, 720, 740],
            "Ordinary Shares Number": [100, 99, 98],
            "Net Debt": [200, 150, 90],
        },
        annual_dates,
    )
    annual_cashflow = statement(
        {
            "Operating Cash Flow": [130, 155, 185],
            "Free Cash Flow": [100, 125, 155],
            "Capital Expenditure": [-30, -30, -30],
        },
        annual_dates,
    )
    quarterly_income = statement(
        {
            "Total Revenue": [180, 190, 200, 210, 215, 225, 235, 245, 250, 260, 270, 280],
            "Net Income": [18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29],
        },
        quarter_dates,
    )
    quarterly_cashflow = statement(
        {"Operating Cash Flow": [25, 27, 29, 31, 32, 34, 36, 38, 40, 42, 44, 46]},
        quarter_dates,
    )
    return MarketData(
        ticker="TEST",
        info={
            "symbol": "TEST",
            "longName": "Test Company",
            "quoteType": "EQUITY",
            "industry": industry,
            "currency": "USD",
            "currentPrice": 150,
            "marketCap": 15000,
            "priceToSalesTrailing12Months": 3.0,
            "trailingPE": 20.0,
            "enterpriseToEbitda": 12.0,
        },
        annual_income=annual_income,
        annual_balance=annual_balance,
        annual_cashflow=annual_cashflow,
        quarterly_income=quarterly_income,
        quarterly_balance=annual_balance,
        quarterly_cashflow=quarterly_cashflow,
        growth_history=pd.DataFrame({"Close": range(100, 136)}, index=price_dates),
        value_history=pd.DataFrame({"Close": range(100, 136)}, index=price_dates),
        analyst_targets={"mean": 180},
        revenue_estimate=pd.DataFrame(),
        earnings_estimate=pd.DataFrame(),
        eps_trend=pd.DataFrame(),
        growth_estimates=pd.DataFrame(),
    )


class FakeProvider:
    def __init__(self, data: MarketData) -> None:
        self.data = data
        self.calls: list[tuple[str, AnalysisRanges]] = []

    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        self.calls.append((ticker_symbol, ranges))
        return self.data


class AnalysisEngineTest(unittest.TestCase):
    def test_engine_runs_complete_industrial_analysis_without_network(self):
        provider = FakeProvider(market_data())
        result = StockAnalysisEngine(provider=provider).analyze(" test ", "2Y", load_config())

        self.assertEqual(result.ticker, "TEST")
        self.assertEqual(result.profile, "Industrial")
        self.assertEqual(set(result.tabs), {"Growth", "Fundamentals", "Value"})
        self.assertIsNotNone(result.overall_score)
        self.assertEqual(result.coverage["confidence"], "High")
        self.assertGreater(result.coverage["percentage"], 85)
        self.assertEqual(provider.calls[0][0], "TEST")
        self.assertEqual(provider.calls[0][1].as_dict(), {"Growth": "2Y", "Fundamentals": "2Y", "Value": "2Y"})

    def test_engine_selects_financial_profile(self):
        result = StockAnalysisEngine(provider=FakeProvider(market_data(industry="Banks - Diversified"))).analyze(
            "TEST",
            {"Growth": "1Y", "Fundamentals": "2Y", "Value": "3Y"},
            load_config(),
        )

        self.assertEqual(result.profile, "Financial")
        value_metric_ids = {metric.id for metric in result.tabs["Value"]["metrics"]}
        self.assertIn("pb_vs_selected_median", value_metric_ids)
        self.assertNotIn("ev_ebitda_vs_selected_median", value_metric_ids)

    def test_engine_exposes_provider_diagnostics_in_missing_warnings(self):
        data = market_data()
        data.diagnostics.append(
            {"source": "earnings estimates", "kind": "provider_error", "message": "upstream failure"}
        )

        result = StockAnalysisEngine(provider=FakeProvider(data)).analyze("TEST", "2Y", load_config())

        self.assertEqual(result.diagnostics[0]["source"], "earnings estimates")
        self.assertTrue(any("earnings estimates failed" in warning for warning in result.missing))

    def test_engine_distinguishes_company_info_provider_failure(self):
        data = market_data()
        data.info = {}
        data.diagnostics.append(
            {"source": "company info", "kind": "network_error", "message": "request timed out"}
        )

        with self.assertRaisesRegex(ValueError, "network_error"):
            StockAnalysisEngine(provider=FakeProvider(data)).analyze("TEST", "2Y", load_config())

    def test_tab_coverage_falls_when_weighted_metric_is_missing(self):
        data = market_data()
        data.annual_income = pd.DataFrame()
        data.quarterly_income = pd.DataFrame()
        result = StockAnalysisEngine(provider=FakeProvider(data)).analyze("TEST", "2Y", load_config())

        growth_coverage = result.tabs["Growth"]["coverage"]
        self.assertLess(growth_coverage["percentage"], 100)
        self.assertLess(result.coverage["percentage"], 100)

    def test_config_normalization_migrates_legacy_metric_ids(self):
        config = load_config()
        config["metrics"]["Value"][0]["id"] = "ps_vs_3y_median"
        config["profile_metrics"]["Financial"]["Value"][0]["id"] = "pe_vs_3y_median"

        normalized = normalize_config(config)

        self.assertEqual(normalized["version"], 2)
        self.assertEqual(normalized["metrics"]["Value"][0]["id"], "ps_vs_selected_median")
        self.assertEqual(
            normalized["profile_metrics"]["Financial"]["Value"][0]["id"],
            "pe_vs_selected_median",
        )


if __name__ == "__main__":
    unittest.main()
