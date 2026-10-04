from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from ticker_analyzer.providers.etf import EtfHoldings, normalize_holding_ticker

JOINT_COLUMNS = ["Ticker", "Company", "Listings", "Joint weight (%)", "ETF count", "ETFs"]

_LEGAL_SUFFIXES = {
    ("inc",),
    ("incorporated",),
    ("corp",),
    ("corporation",),
    ("co",),
    ("company",),
    ("ltd",),
    ("limited",),
    ("plc",),
    ("ag",),
    ("sa",),
    ("s", "a"),
    ("nv",),
    ("n", "v"),
    ("se",),
}
_NAME_ALIASES = {"tsmc": "taiwan semiconductor manufacturing"}
_UNKNOWN_NAMES = {"", "unknown", "unidentified", "n a", "nan", "none"}


def _company_key(value: object) -> str | None:
    """Match naming variants without fuzzy/substring matches between issuers."""
    name = unicodedata.normalize("NFKD", str(value or "").casefold())
    name = "".join(char for char in name if not unicodedata.combining(char))
    words = re.findall(r"[^\W_]+", name)
    while words:
        if words[-1] in {"adr", "adrs", "ads"}:
            words.pop()
            if words and words[-1] in {"sponsored", "unsponsored"}:
                words.pop()
        elif len(words) >= 2 and words[-2] in {"class", "cl"} and len(words[-1]) == 1:
            del words[-2:]
        elif (words[-1],) in _LEGAL_SUFFIXES:
            words.pop()
        elif len(words) >= 2 and tuple(words[-2:]) in _LEGAL_SUFFIXES:
            del words[-2:]
        else:
            break
    key = " ".join(words)
    if key in _UNKNOWN_NAMES:
        return None
    return _NAME_ALIASES.get(key, key)


def _analysis_ticker(tickers: list[str]) -> str:
    # Prefer an available US symbol (including an ADR); never invent a listing
    # that did not occur in the downloaded holdings.
    return next(
        (ticker for ticker in tickers if "." not in ticker and not ticker.isdigit()),
        tickers[0] if tickers else "—",
    )


@dataclass
class JointHoldings:
    holdings: pd.DataFrame
    fund_coverage: dict[str, float]
    missing_funds: list[str]


def combine_etf_holdings(
    results: Mapping[str, EtfHoldings],
    allocations: Mapping[str, float],
) -> JointHoldings:
    """Sum company exposures using portfolio allocations, preserving unpublished exposure."""
    if not allocations or any(not math.isfinite(weight) or weight < 0 for weight in allocations.values()):
        raise ValueError("ETF allocations must be finite, non-negative percentages.")
    if not math.isclose(sum(allocations.values()), 100, abs_tol=0.01):
        raise ValueError("ETF allocations must total 100%.")
    positions = {}
    coverage = {}
    missing = []
    for fund, allocation in allocations.items():
        if allocation == 0:
            continue
        result = results.get(fund)
        if not isinstance(result, EtfHoldings) or result.ticker != fund:
            missing.append(fund)
            continue
        seen = set()
        coverage[fund] = 0.0
        for _, row in result.holdings.iterrows():
            ticker = normalize_holding_ticker(row["Ticker"], result.source)
            name = _company_key(row["Company"])
            raw_symbol = str(row["Ticker"]).strip()
            listing = ticker or re.sub(r"\s+", "", raw_symbol).upper()
            weight = float(row["Weight (%)"])
            if (not name and not ticker) or not math.isfinite(weight) or not 0 < weight <= 100:
                continue
            key = f"name:{name}" if name else f"ticker:{ticker}"
            # Repeated aliases of one listing are duplicates; different listings
            # of the same issuer are separate positions and both contribute.
            duplicate = (key, listing)
            if duplicate in seen:
                continue
            seen.add(duplicate)
            contribution = weight * allocation / 100
            coverage[fund] += contribution
            position = positions.setdefault(
                key,
                {
                    "Company": str(row["Company"]).strip(),
                    "Joint weight (%)": 0.0,
                    "tickers": [],
                    "listings": [],
                    "funds": [],
                },
            )
            position["Joint weight (%)"] += contribution
            if ticker and ticker not in position["tickers"]:
                position["tickers"].append(ticker)
            if raw_symbol and raw_symbol not in position["listings"]:
                position["listings"].append(raw_symbol)
            if fund not in position["funds"]:
                position["funds"].append(fund)
    rows = [
        {
            "Ticker": _analysis_ticker(position["tickers"]),
            "Company": position["Company"],
            "Listings": ", ".join(position["listings"]),
            "Joint weight (%)": position["Joint weight (%)"],
            "ETF count": len(position["funds"]),
            "ETFs": ", ".join(position["funds"]),
        }
        for position in positions.values()
    ]
    frame = (
        pd.DataFrame(rows, columns=JOINT_COLUMNS)
        .sort_values("Joint weight (%)", ascending=False)
        .reset_index(drop=True)
    )
    return JointHoldings(frame, coverage, missing)
