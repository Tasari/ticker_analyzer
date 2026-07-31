from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests

from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData

TIMESERIES_TYPES = {
    "annualTotalRevenue": ("income", "Total Revenue"),
    "annualOperatingRevenue": ("income", "Operating Revenue"),
    "annualNetIncome": ("income", "Net Income"),
    "annualNetIncomeCommonStockholders": ("income", "Net Income Common Stockholders"),
    "annualOperatingIncome": ("income", "Operating Income"),
    "annualEBIT": ("income", "EBIT"),
    "annualEBITDA": ("income", "EBITDA"),
    "annualGrossProfit": ("income", "Gross Profit"),
    "annualInterestExpense": ("income", "Interest Expense"),
    "annualTaxRateForCalcs": ("income", "Tax Rate For Calcs"),
    "annualTotalAssets": ("balance", "Total Assets"),
    "annualTotalDebt": ("balance", "Total Debt"),
    "annualStockholdersEquity": ("balance", "Stockholders Equity"),
    "annualTotalEquityGrossMinorityInterest": ("balance", "Total Equity Gross Minority Interest"),
    "annualInvestedCapital": ("balance", "Invested Capital"),
    "annualNetDebt": ("balance", "Net Debt"),
    "annualCashCashEquivalentsAndShortTermInvestments": ("balance", "Cash Cash Equivalents And Short Term Investments"),
    "annualCashAndCashEquivalents": ("balance", "Cash And Cash Equivalents"),
    "annualOtherShortTermInvestments": ("balance", "Other Short Term Investments"),
    "annualReceivables": ("balance", "Receivables"),
    "annualCurrentAssets": ("balance", "Current Assets"),
    "annualCurrentLiabilities": ("balance", "Current Liabilities"),
    "annualTotalLiabilitiesNetMinorityInterest": ("balance", "Total Liabilities Net Minority Interest"),
    "annualOrdinarySharesNumber": ("balance", "Ordinary Shares Number"),
    "annualShareIssued": ("balance", "Share Issued"),
    "annualOperatingCashFlow": ("cashflow", "Operating Cash Flow"),
    "annualFreeCashFlow": ("cashflow", "Free Cash Flow"),
    "annualCapitalExpenditure": ("cashflow", "Capital Expenditure"),
}


class PublicYahooRankingProvider:
    """Minimal public-endpoint provider used when the crumb-based yfinance client is rate limited."""

    def __init__(self, universe_by_ticker: dict[str, dict[str, Any]], timeout: int = 20) -> None:
        self.universe_by_ticker = universe_by_ticker
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        item = self.universe_by_ticker.get(ticker_symbol, {})
        statements = self._statements(ticker_symbol)
        growth_history, value_history, chart_meta = self._history(
            ticker_symbol, max(_years(value) for value in ranges.as_dict().values())
        )
        current_price = chart_meta.get("regularMarketPrice")
        info = {
            "symbol": ticker_symbol,
            "longName": item.get("company_name") or ticker_symbol,
            "currentPrice": current_price,
            "regularMarketPrice": current_price,
            "marketCap": item.get("market_cap"),
            "industry": item.get("industry") or "",
            "sector": item.get("sector") or "",
            "quoteType": "EQUITY",
            "currency": chart_meta.get("currency") or "USD",
        }
        shares = _latest_value(statements["balance"], "Ordinary Shares Number") or _latest_value(
            statements["balance"], "Share Issued"
        )
        if shares is not None:
            info["sharesOutstanding"] = shares
        empty = pd.DataFrame()
        latest_statement_period = max(
            (
                pd.Timestamp(column).to_pydatetime()
                for frame in statements.values()
                for column in frame.columns
            ),
            default=None,
        )
        latest_price_period = (
            pd.Timestamp(value_history.index.max()).to_pydatetime() if not value_history.empty else None
        )
        return MarketData(
            ticker=ticker_symbol,
            info=info,
            annual_income=statements["income"],
            annual_balance=statements["balance"],
            annual_cashflow=statements["cashflow"],
            quarterly_income=empty,
            quarterly_balance=empty,
            quarterly_cashflow=empty,
            growth_history=growth_history,
            value_history=value_history,
            analyst_targets={},
            revenue_estimate=empty,
            earnings_estimate=empty,
            eps_trend=empty,
            growth_estimates=empty,
            diagnostics=[
                {
                    "source": "batch provider",
                    "kind": "fallback",
                    "message": "Public Yahoo timeseries fallback; analyst consensus data unavailable",
                }
            ],
            provenance={
                "financials": DataProvenance(
                    provider="Yahoo Finance public",
                    source_url="https://query2.finance.yahoo.com/ws/fundamentals-timeseries/",
                    period_end=latest_statement_period,
                    observation_count=sum(frame.size for frame in statements.values()),
                    fallback_level="secondary_source",
                    is_primary_source=False,
                ),
                "prices": DataProvenance(
                    provider="Yahoo Finance public",
                    source_url="https://query1.finance.yahoo.com/v8/finance/chart/",
                    period_end=latest_price_period,
                    observation_count=len(value_history),
                    fallback_level="secondary_source",
                    is_primary_source=False,
                ),
            },
        )

    def _statements(self, ticker: str) -> dict[str, pd.DataFrame]:
        now = int(datetime.now(UTC).timestamp())
        response = self.session.get(
            f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{ticker}",
            params={
                "symbol": ticker,
                "type": ",".join(TIMESERIES_TYPES),
                "period1": now - 7 * 366 * 86400,
                "period2": now + 86400,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        values: dict[str, dict[str, dict[pd.Timestamp, float]]] = {"income": {}, "balance": {}, "cashflow": {}}
        for result in response.json().get("timeseries", {}).get("result", []):
            metric_type = next(iter(result.get("meta", {}).get("type", [])), None)
            if metric_type not in TIMESERIES_TYPES:
                continue
            statement, row_name = TIMESERIES_TYPES[metric_type]
            observations = {}
            for item in result.get(metric_type, []):
                timestamp = item.get("asOfDate") or item.get("reportedValue", {}).get("fmt")
                raw = item.get("reportedValue", {}).get("raw")
                if timestamp and raw is not None:
                    observations[pd.Timestamp(timestamp)] = float(raw)
            if observations:
                values[statement][row_name] = observations
        return {name: _statement_frame(rows) for name, rows in values.items()}

    def _history(self, ticker: str, years: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        response = self.session.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": f"{max(1, years)}y", "interval": "1mo", "events": "div,splits", "includeAdjustedClose": "true"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = next(iter(response.json().get("chart", {}).get("result") or []), {})
        timestamps = result.get("timestamp", [])
        quote = next(iter(result.get("indicators", {}).get("quote", [])), {})
        raw_close = quote.get("close", [])
        adjusted = next(iter(result.get("indicators", {}).get("adjclose", [])), {}).get("adjclose")
        adjusted_close = adjusted or raw_close
        index = pd.to_datetime(timestamps, unit="s", utc=True).tz_localize(None)
        growth = pd.DataFrame({"Close": adjusted_close}, index=index).dropna()
        value = pd.DataFrame({"Close": raw_close}, index=index).dropna()
        return growth, value, result.get("meta", {})


def _statement_frame(rows: dict[str, dict[pd.Timestamp, float]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame.from_dict(rows, orient="index").sort_index(axis=1)


def _latest_value(frame: pd.DataFrame, row: str) -> float | None:
    if frame.empty or row not in frame.index:
        return None
    values = pd.to_numeric(frame.loc[row], errors="coerce").dropna()
    return float(values.iloc[-1]) if not values.empty else None


def _years(label: str) -> int:
    try:
        return int(str(label).upper().removesuffix("Y"))
    except ValueError:
        return 3
