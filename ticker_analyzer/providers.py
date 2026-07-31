from __future__ import annotations

import random
import time
from collections.abc import Iterable
from dataclasses import fields
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
            if isinstance(other, pd.DataFrame) and not other.empty:
                setattr(primary, name, merge_observations(current, other))
        elif isinstance(current, dict) and isinstance(other, dict):
            setattr(primary, name, {**other, **{key: value for key, value in current.items() if value is not None}})
    primary.provenance = {**fallback.provenance, **primary.provenance}
    primary.official_ids = {**fallback.official_ids, **primary.official_ids}


def merge_observations(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    """Merge statement observations cell-by-cell, preserving provider priority."""
    if primary.empty:
        return fallback.copy()
    merged = primary.combine_first(fallback).sort_index(axis=1)
    provenance: dict[Any, Any] = {}
    reconciliation = [
        *fallback.attrs.get("reconciliation", []),
        *primary.attrs.get("reconciliation", []),
    ]
    fallback_provenance = fallback.attrs.get("observation_provenance", {})
    primary_provenance = primary.attrs.get("observation_provenance", {})
    for row in merged.index:
        for period in merged.columns:
            key = (row, period)
            if row in primary.index and period in primary.columns and pd.notna(primary.at[row, period]):
                if row in fallback.index and period in fallback.columns and pd.notna(fallback.at[row, period]):
                    left = pd.to_numeric(pd.Series([primary.at[row, period]]), errors="coerce").iloc[0]
                    right = pd.to_numeric(pd.Series([fallback.at[row, period]]), errors="coerce").iloc[0]
                    if pd.notna(left) and pd.notna(right):
                        scale = max(abs(float(left)), abs(float(right)), 1.0)
                        reconciliation.append(
                            {
                                "fact": str(row),
                                "period_end": _period_label(period),
                                "relative_difference": abs(float(left) - float(right)) / scale,
                            }
                        )
                if key in primary_provenance:
                    provenance[key] = primary_provenance[key]
            elif key in fallback_provenance:
                provenance[key] = fallback_provenance[key]
    merged.attrs.update(fallback.attrs)
    merged.attrs.update(primary.attrs)
    merged.attrs["observation_provenance"] = provenance
    merged.attrs["filed_dates"] = {**fallback.attrs.get("filed_dates", {}), **primary.attrs.get("filed_dates", {})}
    merged.attrs["reconciliation"] = reconciliation
    return merged


def _period_label(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return str(value) if pd.isna(parsed) else pd.Timestamp(parsed).date().isoformat()


class JsonApiClient:
    _host_last_request: dict[str, float] = {}
    _rate_lock = Lock()

    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout: float = 20,
        minimum_interval: float = 0.1,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.minimum_interval = max(0.0, minimum_interval)
        self._cache: dict[str, tuple[str | None, Any]] = {}
        retry = Retry(
            total=4,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        if hasattr(self.session, "mount"):
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        self._wait_for_host(url)
        headers = dict(kwargs.pop("headers", {}) or {})
        params = kwargs.get("params") or {}
        cache_key = f"{url}?{repr(sorted(params.items()))}"
        cached = self._cache.get(cache_key)
        if cached and cached[0]:
            headers["If-None-Match"] = cached[0]
        response = self.session.get(url, timeout=self.timeout, headers=headers, **kwargs)
        if response.status_code == 304 and cached:
            return cached[1]
        response.raise_for_status()
        payload = response.json()
        etag = response.headers.get("ETag")
        if etag:
            self._cache[cache_key] = (etag, payload)
        return payload

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._rate_lock:
            now = time.monotonic()
            wait = self.minimum_interval - (now - self._host_last_request.get(host, 0.0))
            if wait > 0:
                time.sleep(wait + random.uniform(0, min(0.025, self.minimum_interval)))
            self._host_last_request[host] = time.monotonic()


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
            "Total Debt": ["LongTermDebtAndFinanceLeaseObligations"],
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
        annual_forms = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
        quarterly_forms = {"10-Q", "10-Q/A"}
        annual_income = sec_statement(
            facts_payload, self.TAGS["annual_income"], forms=annual_forms, as_of=ranges.data_as_of
        )
        annual_balance = sec_statement(
            facts_payload, self.TAGS["annual_balance"], forms=annual_forms, as_of=ranges.data_as_of
        )
        annual_cashflow = sec_statement(
            facts_payload,
            self.TAGS["annual_cashflow"],
            forms=annual_forms,
            negative_rows={"Capital Expenditure"},
            as_of=ranges.data_as_of,
        )
        quarter_and_year_forms = quarterly_forms | annual_forms
        quarterly_income = sec_statement(
            facts_payload,
            self.TAGS["annual_income"],
            forms=quarter_and_year_forms,
            quarterly=True,
            as_of=ranges.data_as_of,
        )
        quarterly_balance = sec_statement(
            facts_payload,
            self.TAGS["annual_balance"],
            forms=quarter_and_year_forms,
            quarterly=True,
            as_of=ranges.data_as_of,
        )
        quarterly_cashflow = sec_statement(
            facts_payload,
            self.TAGS["annual_cashflow"],
            forms=quarter_and_year_forms,
            negative_rows={"Capital Expenditure"},
            quarterly=True,
            as_of=ranges.data_as_of,
        )
        filing = latest_sec_filing(
            facts_payload, forms=annual_forms | quarterly_forms, as_of=ranges.data_as_of
        )
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
    quarterly: bool = False,
    as_of: str | datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    us_gaap = payload.get("facts", {}).get("us-gaap", {})
    result: dict[str, pd.Series] = {}
    filed_dates: dict[pd.Timestamp, pd.Timestamp] = {}
    observation_provenance: dict[tuple[str, pd.Timestamp], list[dict[str, Any]]] = {}
    cutoff = pd.to_datetime(as_of, utc=True, errors="coerce") if as_of is not None else None
    for row_name, tags in rows.items():
        records: list[dict[str, Any]] = []
        for tag in tags:
            units = us_gaap.get(tag, {}).get("units", {})
            candidates = units.get("USD") or units.get("shares") or []
            records = [
                {**record, "_tag": tag}
                for record in candidates
                if record.get("form") in forms and record.get("end")
            ]
            if cutoff is not None:
                records = [record for record in records if _filed_on_or_before(record, cutoff)]
            if records:
                break
        if row_name == "Total Debt" and not records:
            records = total_debt_component_records(us_gaap, forms, cutoff)
        if not records:
            continue
        if quarterly:
            records = discrete_quarter_records(records)
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
            versions_for_period = [
                version
                for observation in records
                if pd.to_datetime(observation.get("end"), errors="coerce") == period
                for version in observation.get("_versions", [observation])
            ]
            observation_provenance[(row_name, period)] = [
                {
                    "provider": "SEC",
                    "filed_at": version.get("filed"),
                    "form": version.get("form"),
                    "accession_number": version.get("accn"),
                    "tag": version.get("_tag"),
                    "value": version.get("val"),
                }
                for version in versions_for_period
            ]
    if not result:
        return pd.DataFrame()
    frame = pd.DataFrame(result).T.sort_index(axis=1)
    frame.attrs["filed_dates"] = filed_dates
    frame.attrs["observation_provenance"] = observation_provenance
    return frame


def latest_sec_filing(
    payload: dict[str, Any], *, forms: set[str] | None = None, as_of: datetime | pd.Timestamp | None = None
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    cutoff = pd.to_datetime(as_of, utc=True, errors="coerce") if as_of is not None else None
    for fact in payload.get("facts", {}).get("us-gaap", {}).values():
        for values in fact.get("units", {}).values():
            records.extend(
                item
                for item in values
                if item.get("filed")
                and (forms is None or item.get("form") in forms)
                and (cutoff is None or _filed_on_or_before(item, cutoff))
            )
    return max(records, key=lambda item: str(item.get("filed", "")), default={})


def _filed_on_or_before(record: dict[str, Any], cutoff: pd.Timestamp) -> bool:
    filed = pd.to_datetime(record.get("filed"), utc=True, errors="coerce")
    return not pd.isna(filed) and filed <= cutoff


def discrete_quarter_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert cumulative SEC cash-flow/income facts to discrete quarters."""
    versions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (str(record.get("start", "")), str(record.get("end", "")))
        versions.setdefault(key, []).append(record)
    latest = {
        key: {
            **max(items, key=lambda item: str(item.get("filed", ""))),
            "_versions": items,
        }
        for key, items in versions.items()
    }
    ordered = sorted(latest.values(), key=lambda item: (str(item.get("start", "")), str(item.get("end", ""))))
    output: list[dict[str, Any]] = []
    cumulative: dict[str, list[dict[str, Any]]] = {}
    for record in ordered:
        if not record.get("start"):
            output.append(record)
            continue
        start = pd.to_datetime(record.get("start"), errors="coerce")
        end = pd.to_datetime(record.get("end"), errors="coerce")
        if pd.isna(start) or pd.isna(end):
            continue
        duration = (end - start).days
        if duration <= 120:
            output.append(record)
        else:
            prior_candidates = cumulative.get(str(record.get("start")), [])
            prior = max(
                (candidate for candidate in prior_candidates if str(candidate.get("end", "")) < str(record.get("end", ""))),
                key=lambda item: str(item.get("end", "")),
                default=None,
            )
            if prior is not None and record.get("val") is not None and prior.get("val") is not None:
                derived = dict(record)
                derived["val"] = float(record["val"]) - float(prior["val"])
                derived["start"] = prior.get("end")
                derived["_derived_from_ytd"] = True
                output.append(derived)
        cumulative.setdefault(str(record.get("start")), []).append(record)
    return output


def total_debt_component_records(
    us_gaap: dict[str, Any], forms: set[str], cutoff: pd.Timestamp | None
) -> list[dict[str, Any]]:
    """Build total debt only when a complete debt fact is unavailable."""
    component_tags = (
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtCurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtNoncurrent",
        "FinanceLeaseLiabilityCurrent",
        "FinanceLeaseLiabilityNoncurrent",
        "ShortTermBorrowings",
    )
    by_period: dict[str, dict[str, dict[str, Any]]] = {}
    for tag in component_tags:
        units = us_gaap.get(tag, {}).get("units", {})
        for record in units.get("USD", []):
            if record.get("form") not in forms or not record.get("end"):
                continue
            if cutoff is not None and not _filed_on_or_before(record, cutoff):
                continue
            key = str(record.get("end"))
            previous = by_period.setdefault(key, {}).get(tag)
            if previous is None or str(record.get("filed", "")) >= str(previous.get("filed", "")):
                by_period[key][tag] = {**record, "_tag": tag}
    result: list[dict[str, Any]] = []
    for components in by_period.values():
        # Avoid double-counting overlapping current/non-current tag variants.
        chosen = []
        current_includes_lease = "LongTermDebtAndFinanceLeaseObligationsCurrent" in components
        noncurrent_includes_lease = "LongTermDebtAndFinanceLeaseObligationsNoncurrent" in components
        alternatives_to_sum = [
            ("LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"),
            ("LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent"),
            ("ShortTermBorrowings",),
        ]
        if not current_includes_lease:
            alternatives_to_sum.append(("FinanceLeaseLiabilityCurrent",))
        if not noncurrent_includes_lease:
            alternatives_to_sum.append(("FinanceLeaseLiabilityNoncurrent",))
        for alternatives in alternatives_to_sum:
            item = next((components[tag] for tag in alternatives if tag in components), None)
            if item is not None:
                chosen.append(item)
        if chosen:
            newest = max(chosen, key=lambda item: str(item.get("filed", "")))
            combined = dict(newest)
            combined["val"] = sum(float(item.get("val", 0)) for item in chosen)
            combined["_tag"] = "+".join(item.get("_tag", "component") for item in chosen)
            combined["_versions"] = chosen
            result.append(combined)
    return result


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
    def __init__(self, *, sandbox: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sandbox = sandbox

    def broker_dealers(self, *, crd_number: str | int) -> Any:
        dataset = "brokerDealerFirmListMock" if self.sandbox else "brokerDealerFirmList"
        return self.get_json(
            f"https://api.finra.org/data/group/registration/name/{dataset}",
            params={"firmCrdNumber": crd_number},
            headers={"Accept": "application/json"},
        )
