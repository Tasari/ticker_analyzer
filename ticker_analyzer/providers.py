from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import requests

from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData


class CompositeProvider:
    """Merge providers in priority order while retaining source diagnostics.

    Earlier providers win for populated scalar fields. Later providers fill gaps,
    which makes an official-source provider + yfinance fallback deterministic.
    """

    def __init__(self, providers: Iterable[Any]) -> None:
        self.providers = list(providers)
        if not self.providers:
            raise ValueError("CompositeProvider requires at least one provider.")

    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        results: list[MarketData] = []
        failures: list[dict[str, str]] = []
        for provider in self.providers:
            try:
                results.append(provider.fetch(ticker_symbol, ranges))
            except Exception as exc:
                failures.append(
                    {
                        "source": type(provider).__name__,
                        "kind": "provider_error",
                        "message": (str(exc).strip() or type(exc).__name__)[:240],
                    }
                )
        if not results:
            raise RuntimeError(f"All market-data providers failed for {ticker_symbol}.")
        merged = results[0]
        for fallback in results[1:]:
            merge_market_data(merged, fallback)
        merged.diagnostics = failures + [item for result in results for item in result.diagnostics]
        return merged


def merge_market_data(primary: MarketData, fallback: MarketData) -> None:
    for field in fields(MarketData):
        name = field.name
        if name in {"ticker", "diagnostics", "provenance", "official_ids"}:
            continue
        current = getattr(primary, name)
        other = getattr(fallback, name)
        if isinstance(current, pd.DataFrame):
            if current.empty and isinstance(other, pd.DataFrame) and not other.empty:
                setattr(primary, name, other.copy())
        elif isinstance(current, dict) and isinstance(other, dict):
            setattr(primary, name, {**other, **{key: value for key, value in current.items() if value is not None}})
    primary.provenance = {**fallback.provenance, **primary.provenance}
    primary.official_ids = {**fallback.official_ids, **primary.official_ids}


class JsonApiClient:
    def __init__(self, *, session: requests.Session | None = None, timeout: float = 20) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout

    def get_json(self, url: str, **kwargs: Any) -> Any:
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()


class SecClient(JsonApiClient):
    """Minimal SEC submissions/companyfacts client with compliant identification."""

    def __init__(self, user_agent: str, **kwargs: Any) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC User-Agent must identify the application and a contact email.")
        super().__init__(**kwargs)
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}

    def submissions(self, cik: str | int) -> dict[str, Any]:
        return self.get_json(
            f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
            headers=self.headers,
        )

    def company_facts(self, cik: str | int) -> dict[str, Any]:
        return self.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            headers=self.headers,
        )

    def ticker_map(self) -> dict[str, Any]:
        return self.get_json("https://www.sec.gov/files/company_tickers.json", headers=self.headers)


class SecCompanyFactsProvider:
    """Primary-source US-GAAP statements suitable for a CompositeProvider."""

    TAGS = {
        "annual_income": {
            "Total Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
            "Net Income": ["NetIncomeLoss", "ProfitLoss"],
            "Operating Income": ["OperatingIncomeLoss"],
            "Gross Profit": ["GrossProfit"],
            "Interest Expense": ["InterestExpenseNonOperating", "InterestExpense"],
        },
        "annual_balance": {
            "Total Assets": ["Assets"],
            "Total Debt": ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"],
            "Current Assets": ["AssetsCurrent"],
            "Current Liabilities": ["LiabilitiesCurrent"],
            "Cash And Cash Equivalents": ["CashAndCashEquivalentsAtCarryingValue"],
            "Stockholders Equity": ["StockholdersEquity"],
            "Ordinary Shares Number": ["CommonStockSharesOutstanding"],
        },
        "annual_cashflow": {
            "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities"],
            "Capital Expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment"],
        },
    }

    def __init__(self, client: SecClient) -> None:
        self.client = client
        self._ticker_map: dict[str, dict[str, Any]] | None = None

    def fetch(self, ticker_symbol: str, ranges: AnalysisRanges) -> MarketData:
        company = self._resolve_company(ticker_symbol)
        cik = int(company["cik_str"])
        facts_payload = self.client.company_facts(cik)
        submissions = self.client.submissions(cik)
        annual_income = sec_statement(facts_payload, self.TAGS["annual_income"], forms={"10-K", "20-F", "40-F"})
        annual_balance = sec_statement(facts_payload, self.TAGS["annual_balance"], forms={"10-K", "20-F", "40-F"})
        annual_cashflow = sec_statement(
            facts_payload,
            self.TAGS["annual_cashflow"],
            forms={"10-K", "20-F", "40-F"},
            negative_rows={"Capital Expenditure"},
        )
        quarterly_income = sec_statement(facts_payload, self.TAGS["annual_income"], forms={"10-Q"})
        quarterly_balance = sec_statement(facts_payload, self.TAGS["annual_balance"], forms={"10-Q"})
        quarterly_cashflow = sec_statement(
            facts_payload,
            self.TAGS["annual_cashflow"],
            forms={"10-Q"},
            negative_rows={"Capital Expenditure"},
        )
        filing = latest_sec_filing(facts_payload)
        provenance = DataProvenance(
            provider="SEC",
            fetched_at=datetime.now(UTC),
            source_url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
            period_end=_utc_datetime(filing.get("end")),
            filed_at=_utc_datetime(filing.get("filed")),
            form=filing.get("form"),
            accession_number=filing.get("accn"),
            observation_count=max(len(annual_income.columns), len(quarterly_income.columns)),
            is_primary_source=True,
        )
        return empty_market_data(
            ticker_symbol,
            info={
                "symbol": ticker_symbol,
                "longName": submissions.get("name") or company.get("title"),
                "quoteType": "EQUITY",
            },
            annual_income=annual_income,
            annual_balance=annual_balance,
            annual_cashflow=annual_cashflow,
            quarterly_income=quarterly_income,
            quarterly_balance=quarterly_balance,
            quarterly_cashflow=quarterly_cashflow,
            provenance={"financials": provenance},
            official_ids={"cik": str(cik), "sec_sic": submissions.get("sic")},
        )

    def _resolve_company(self, ticker_symbol: str) -> dict[str, Any]:
        if self._ticker_map is None:
            payload = self.client.ticker_map()
            self._ticker_map = {str(item.get("ticker", "")).upper(): item for item in payload.values()}
        company = self._ticker_map.get(ticker_symbol.upper())
        if not company:
            raise ValueError(f"SEC CIK not found for {ticker_symbol}.")
        return company


def empty_market_data(ticker: str, **overrides: Any) -> MarketData:
    values: dict[str, Any] = {
        "ticker": ticker,
        "info": {},
        "annual_income": pd.DataFrame(),
        "annual_balance": pd.DataFrame(),
        "annual_cashflow": pd.DataFrame(),
        "quarterly_income": pd.DataFrame(),
        "quarterly_balance": pd.DataFrame(),
        "quarterly_cashflow": pd.DataFrame(),
        "growth_history": pd.DataFrame(),
        "value_history": pd.DataFrame(),
        "analyst_targets": {},
        "revenue_estimate": pd.DataFrame(),
        "earnings_estimate": pd.DataFrame(),
        "eps_trend": pd.DataFrame(),
        "growth_estimates": pd.DataFrame(),
    }
    values.update(overrides)
    return MarketData(**values)


def sec_statement(
    payload: dict[str, Any],
    rows: dict[str, list[str]],
    *,
    forms: set[str],
    negative_rows: set[str] | None = None,
) -> pd.DataFrame:
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    result: dict[str, pd.Series] = {}
    filed_dates: dict[pd.Timestamp, pd.Timestamp] = {}
    for row_name, tags in rows.items():
        records: list[dict[str, Any]] = []
        for tag in tags:
            units = us_gaap.get(tag, {}).get("units", {})
            candidates = units.get("USD") or units.get("shares") or []
            records = [record for record in candidates if record.get("form") in forms and record.get("end")]
            if forms == {"10-Q"}:
                records = [record for record in records if is_discrete_quarter_or_instant(record)]
            if records:
                break
        if not records:
            continue
        by_period: dict[pd.Timestamp, dict[str, Any]] = {}
        for record in records:
            period = pd.to_datetime(record.get("end"), errors="coerce")
            if pd.isna(period):
                continue
            previous = by_period.get(period)
            if previous is None or str(record.get("filed", "")) >= str(previous.get("filed", "")):
                by_period[period] = record
        multiplier = -1 if row_name in (negative_rows or set()) else 1
        result[row_name] = pd.Series(
            {period: multiplier * float(record["val"]) for period, record in by_period.items() if record.get("val") is not None}
        )
        for period, record in by_period.items():
            filed = pd.to_datetime(record.get("filed"), errors="coerce")
            if not pd.isna(filed) and (period not in filed_dates or filed > filed_dates[period]):
                filed_dates[period] = pd.Timestamp(filed)
    if not result:
        return pd.DataFrame()
    frame = pd.DataFrame(result).T.sort_index(axis=1)
    frame.attrs["filed_dates"] = filed_dates
    return frame


def latest_sec_filing(payload: dict[str, Any]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for fact in payload.get("facts", {}).get("us-gaap", {}).values():
        for values in fact.get("units", {}).values():
            records.extend(item for item in values if item.get("filed"))
    return max(records, key=lambda item: str(item.get("filed", "")), default={})


def is_discrete_quarter_or_instant(record: dict[str, Any]) -> bool:
    if not record.get("start"):
        return True
    start = pd.to_datetime(record.get("start"), errors="coerce")
    end = pd.to_datetime(record.get("end"), errors="coerce")
    if pd.isna(start) or pd.isna(end):
        return False
    duration = (end - start).days
    return 60 <= duration <= 120


def _utc_datetime(value: Any) -> datetime | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed).to_pydatetime().replace(tzinfo=UTC)


class NbpClient(JsonApiClient):
    def exchange_rate(self, currency: str, *, table: str = "A", top_count: int = 1) -> dict[str, Any]:
        return self.get_json(
            f"https://api.nbp.pl/api/exchangerates/rates/{table}/{currency.lower()}/last/{top_count}/",
            headers={"Accept": "application/json"},
        )

    def exchange_rates(self, currency: str, start: str, end: str, *, table: str = "A") -> list[dict[str, Any]]:
        payload = self.get_json(
            f"https://api.nbp.pl/api/exchangerates/rates/{table}/{currency.lower()}/{start}/{end}/",
            params={"format": "json"},
            headers={"Accept": "application/json"},
        )
        return list(payload.get("rates", []))


class FdicClient(JsonApiClient):
    def institutions(self, *, cert: str | int, limit: int = 100) -> dict[str, Any]:
        return self.get_json(
            "https://banks.data.fdic.gov/api/institutions",
            params={"filters": f"CERT:{cert}", "format": "json", "limit": limit},
        )

    def financials(
        self,
        *,
        cert: str | int,
        fields: str = "CERT,REPDTE,ASSET,DEP,NETINC,ROA",
        limit: int = 8,
    ) -> dict[str, Any]:
        return self.get_json(
            "https://banks.data.fdic.gov/api/financials",
            params={
                "filters": f"CERT:{cert}",
                "fields": fields,
                "sort_by": "REPDTE",
                "sort_order": "DESC",
                "format": "json",
                "limit": limit,
            },
        )


class GleifClient(JsonApiClient):
    def lei_records(self, *, legal_name: str, page_size: int = 10) -> dict[str, Any]:
        return self.get_json(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": legal_name, "page[size]": page_size},
            headers={"Accept": "application/vnd.api+json"},
        )


class FinraClient(JsonApiClient):
    def broker_dealers(self, *, crd_number: str | int) -> Any:
        return self.get_json(
            "https://api.finra.org/data/group/registration/name/brokerDealerFirmListMock",
            params={"firmCrdNumber": crd_number},
            headers={"Accept": "application/json"},
        )
