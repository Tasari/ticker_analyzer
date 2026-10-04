from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import pandas as pd
import requests
import yfinance as yf

from ticker_analyzer.ticker_symbols import normalize_ticker

PUBLIC_EXCHANGES = {
    ".DE": "etr", ".L": "lon", ".PA": "epa", ".MI": "bit",
    ".AS": "ams", ".SW": "swx", ".TO": "tsx", ".AX": "asx",
}


class EtfDataError(ValueError):
    pass


class NotAnEtfError(EtfDataError):
    pass


@dataclass
class EtfHoldings:
    ticker: str
    holdings: pd.DataFrame
    source: str
    source_url: str
    fetched_at: datetime
    as_of: str | None = None
    warnings: tuple[str, ...] = ()


def normalize_holdings(frame: pd.DataFrame, *, fractions: bool) -> pd.DataFrame:
    """Keep original fund weights; never renormalize a partial list to 100%."""
    if frame.empty:
        raise EtfDataError("No holdings were published for this ETF.")
    if not {"Name", "Holding Percent"}.issubset(frame.columns):
        raise EtfDataError("The provider returned an unsupported holdings format.")
    data = frame.reset_index() if "Symbol" not in frame.columns else frame.copy()
    if "Symbol" not in data:
        data["Symbol"] = "—"
    weights = data["Holding Percent"].map(lambda value: value.get("raw") if isinstance(value, dict) else value)
    if not fractions:
        weights = weights.astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False)
    weights = pd.to_numeric(weights, errors="coerce") * (100 if fractions else 1)
    result = pd.DataFrame({
        "Ticker": data["Symbol"].fillna("—").astype(str),
        "Company": data["Name"].fillna("").astype(str),
        "Weight (%)": weights,
    })
    result = result[result["Weight (%)"].gt(0) & result["Weight (%)"].le(100) & result["Company"].str.strip().ne("")]
    result = result.sort_values("Weight (%)", ascending=False).drop_duplicates(["Ticker", "Company"])
    if result.empty or result["Weight (%)"].sum() > 100.5:
        raise EtfDataError("The provider did not return valid fund allocation percentages.")
    return result.head(25).reset_index(drop=True)


def fetch_etf_holdings(symbol: str) -> EtfHoldings:
    ticker = normalize_ticker(symbol)
    if not ticker or ticker == "ACC_STMT":
        raise EtfDataError("Enter a valid ETF ticker, including its market suffix when needed.")
    try:
        funds = yf.Ticker(ticker).funds_data
        quote_type = funds.quote_type()
        if quote_type and quote_type != "ETF":
            raise NotAnEtfError(f"{ticker} is not classified as an ETF. Enter an ETF ticker.")
        holdings = normalize_holdings(funds.top_holdings, fractions=True)
        return EtfHoldings(ticker, holdings, "Yahoo Finance", f"https://finance.yahoo.com/quote/{ticker}/holdings/", datetime.now(UTC))
    except NotAnEtfError:
        raise
    except Exception:
        # FundsData depends on Yahoo's quoteSummary cookie/crumb. The public
        # holdings table below provides an independent path for supported venues.
        pass
    if public_holdings_location(ticker):
        try:
            return fetch_public_etf_holdings(ticker)
        except (requests.RequestException, EtfDataError):
            pass
    raise EtfDataError(
        f"Holdings are unavailable for {ticker}. Check the ETF ticker and market suffix, or try again later. "
        "Some ETFs do not publish holdings through these sources."
    )


class HoldingsTableParser(HTMLParser):
    """Read only public HTML table cells and visible text, excluding scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.visible_text: list[str] = []
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag == "table":
            self.table = []
        elif tag == "tr" and self.table is not None:
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.table is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, data):
        if self.hidden:
            return
        self.visible_text.append(data.strip())
        if self.cell is not None:
            self.cell.append(data)


def public_holdings_location(ticker: str) -> tuple[str, str] | None:
    """Map the exact listing, retaining exchange identity instead of substituting funds."""
    for suffix, exchange in PUBLIC_EXCHANGES.items():
        if ticker.endswith(suffix):
            local = ticker[:-len(suffix)]
            if re.fullmatch(r"[A-Z0-9-]{1,16}", local):
                return f"https://stockanalysis.com/quote/{exchange}/{local}/holdings/", f"{exchange.upper()}:{local}"
            return None
    if re.fullmatch(r"[A-Z0-9-]{1,12}", ticker):
        return f"https://stockanalysis.com/etf/{ticker.lower()}/holdings/", ticker
    return None


def fetch_public_etf_holdings(ticker: str) -> EtfHoldings:
    location = public_holdings_location(ticker)
    if location is None:
        raise EtfDataError("This exchange has no supported public holdings source.")
    url, page_symbol = location
    response = requests.get(url, timeout=15, headers={"User-Agent": "TickerAnalyzer/0.1"})
    response.raise_for_status()
    parser = HoldingsTableParser()
    parser.feed(response.text)
    text = " ".join(" ".join(parser.visible_text).split())
    if not re.search(rf"\b{re.escape(page_symbol)} Holdings Information\b", text, re.IGNORECASE):
        raise EtfDataError("The public page did not match the requested ETF.")
    for table in parser.tables:
        if table:
            table[0] = ["Weight" if header == "% Weight" else header for header in table[0]]
        if not table or not {"Symbol", "Name", "Weight"}.issubset(table[0]):
            continue
        rows = [row for row in table[1:] if len(row) == len(table[0])]
        frame = pd.DataFrame(rows, columns=table[0]).rename(columns={"Weight": "Holding Percent"})
        holdings = normalize_holdings(frame, fractions=False)
        as_of = re.search(r"As of ([A-Z][a-z]{2} \d{1,2}, \d{4})", text)
        return EtfHoldings(
            ticker, holdings, "Stock Analysis / Finnhub", url, datetime.now(UTC),
            as_of=as_of.group(1) if as_of else None,
            warnings=("Yahoo holdings were unavailable; showing the public Stock Analysis holdings table.",),
        )
    raise EtfDataError("No public holdings table was available.")
