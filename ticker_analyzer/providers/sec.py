from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import pandas as pd

from ticker_analyzer.domain import AnalysisRanges, DataProvenance, MarketData
from ticker_analyzer.providers.http import JsonApiClient


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

    TAGS: ClassVar[dict[str, dict[str, list[str]]]] = {
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
