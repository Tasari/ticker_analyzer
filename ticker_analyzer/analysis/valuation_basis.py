"""Keep statement currency and ordinary-share units explicit during valuation."""
from __future__ import annotations

from typing import Any

import pandas as pd

from ticker_analyzer.domain import MarketData
from ticker_analyzer.markets import currency_convention
from ticker_analyzer.numbers import clean_number
from ticker_analyzer.providers.fx import exchange_rate, rates_on_dates

# Effective dates bound historical assumptions; overrides must provide their
# validity start instead of inferring a ratio from prices or share counts.
ADR_PROGRAMS = {
    "TSM": (5.0, "2003-01-01", "https://www.sec.gov/Archives/edgar/data/1046179/000095016803002302/d424b1.htm"),
    "FUTU": (8.0, "2019-03-08", "https://ir.futuholdings.com/news-releases/news-release-details/futu-announces-pricing-initial-public-offering/"),
}


def share_basis(ticker: str, info: dict[str, Any]) -> tuple[float | None, str, str]:
    ratio = clean_number(info.get("ordinarySharesPerReceipt"))
    if ratio is not None and ratio > 0:
        return ratio, str(info.get("shareRatioEffectiveFrom") or ""), "provider instrument metadata"
    if ticker in ADR_PROGRAMS:
        return ADR_PROGRAMS[ticker]
    description = str(info.get("longName") or "").upper()
    foreign_us_listing = (
        not any(char in ticker for char in ".=^")
        and info.get("country") not in (None, "", "United States", "USA")
    )
    if info.get("isAdr") or "DEPOSITARY" in description or foreign_us_listing:
        return None, "", "ordinary shares per listed receipt unavailable"
    return 1.0, "", "ordinary listed share"


def prepare_valuation_basis(data: MarketData) -> pd.DataFrame:
    """Return valuation-only history in reporting currency per ordinary share.

    Display prices, targets, growth histories and statements retain their units.
    Current capitalization comes from the provider when available; it is already
    an issuer total and must never be divided by the ADR ratio again.
    """
    info = data.info
    quote = currency_convention(info.get("currency"))[0]
    currencies = {
        str(frame.attrs["financial_currency"])
        for frame in (data.annual_income, data.annual_balance, data.annual_cashflow,
                      data.quarterly_income, data.quarterly_balance, data.quarterly_cashflow)
        if not frame.empty and frame.attrs.get("financial_currency")
    }
    declared = currency_convention(info.get("financialCurrency"))[0]
    if declared:
        currencies.add(declared)
    reporting = next(iter(currencies)) if len(currencies) == 1 and "MIXED" not in currencies else ""
    ratio, effective, source = share_basis(data.ticker, info)
    info["valuationOrdinarySharesPerReceipt"] = ratio
    info["shareRatioSource"] = source
    info["valuationReportingCurrency"] = reporting
    info["valuationBasisPrepared"] = True
    current_fx = exchange_rate(quote, reporting) if quote and reporting else None
    info["quoteToReportingFx"] = current_fx
    cap = clean_number(info.get("marketCap"))
    if cap is None and ratio:
        from ticker_analyzer.metrics.utils import latest_row_value

        shares = latest_row_value(data.annual_balance, ["Ordinary Shares Number", "Share Issued"])
        price = clean_number(info.get("currentPrice"))
        if shares is not None and price is not None:
            cap = price * shares / ratio
            info["marketCap"] = cap
    info["marketCapReporting"] = cap * current_fx if cap is not None and current_fx is not None else None
    info["marketCapCurrency"] = quote
    info["valuationBasis"] = {
        "quote_currency": quote, "reporting_currency": reporting,
        "ordinary_shares_per_receipt": ratio, "share_ratio_effective_from": effective,
        "share_ratio_source": source, "quote_to_reporting_fx": current_fx,
        "fx_source": "Yahoo Finance daily FX, direct or via USD; last available rate within 7 days",
    }
    notes = []
    if not reporting:
        notes.append("Reporting currency is missing or inconsistent; statement-based valuation is unavailable.")
    elif current_fx is None:
        notes.append(f"No recent {quote}/{reporting} FX rate; statement-based current valuation is unavailable.")
    if ratio is None:
        notes.append("Share/ADR conversion is unverified; valuation using ordinary share counts is unavailable.")
    history = data.value_history.copy()
    if not history.empty and "Close" in history:
        factors = rates_on_dates(quote, reporting, history.index)
        history["Close"] = pd.to_numeric(history["Close"], errors="coerce") * factors / (ratio or float("nan"))
        if effective:
            history.loc[pd.to_datetime(history.index).tz_localize(None) < pd.Timestamp(effective), "Close"] = float("nan")
        elif ratio and ratio != 1:
            history["Close"] = float("nan")
            notes.append("ADR ratio validity date is missing; historical share-based valuation is unavailable.")
        if ratio and reporting and factors.isna().any():
            notes.append("Historical FX is incomplete; affected valuation observations are excluded.")
    info["valuationNotes"] = notes
    data.diagnostics.extend({"source": "valuation basis", "kind": "missing_data", "message": note} for note in notes)
    return history


def reporting_market_cap(info: dict[str, Any]) -> float | None:
    if info.get("valuationBasisPrepared"):
        return clean_number(info.get("marketCapReporting"))
    quote = currency_convention(info.get("currency"))[0]
    reporting = currency_convention(info.get("financialCurrency"))[0]
    if quote and reporting and quote != reporting:
        return None
    return clean_number(info.get("marketCap"))
