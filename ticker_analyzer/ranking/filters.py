from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ticker_analyzer.numbers import clean_number


@dataclass(frozen=True)
class RankingFilters:
    query: str = ""
    countries: tuple[str, ...] = ()
    markets: tuple[str, ...] = ()
    exchanges: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()
    ratings: tuple[str, ...] = ()
    confidences: tuple[str, ...] = ()
    overall_score_range: tuple[float, float] = (0.0, 100.0)
    minimum_growth: float = 0.0
    minimum_fundamentals: float = 0.0
    minimum_value: float = 0.0
    minimum_quality: float = 0.0
    minimum_market_cap: float = 0.0
    include_unscored: bool = True


def filter_ranking_companies(
    companies: list[dict[str, Any]],
    filters: RankingFilters,
) -> list[dict[str, Any]]:
    query = filters.query.strip().casefold()
    return [
        row
        for row in companies
        if _matches_query(row, query)
        and _matches_choice(row, "country", filters.countries)
        and _matches_choice(row, "market", filters.markets)
        and _matches_choice(row, "exchange", filters.exchanges)
        and _matches_choice(row, "sector", filters.sectors)
        and _matches_choice(row, "profile", filters.profiles)
        and _matches_choice(row, "rating", filters.ratings)
        and _matches_choice(row, "rating_confidence", filters.confidences)
        and _matches_overall_score(row, filters)
        and _meets_minimum(row.get("growth_score"), filters.minimum_growth)
        and _meets_minimum(row.get("fundamentals_score"), filters.minimum_fundamentals)
        and _meets_minimum(row.get("value_score"), filters.minimum_value)
        and _meets_minimum(
            row.get("data_quality", row.get("confidence")),
            filters.minimum_quality,
        )
        and _meets_minimum(row.get("market_cap"), filters.minimum_market_cap)
    ]


def _matches_query(row: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        str(row.get(field) or "")
        for field in ("ticker", "company_name", "industry")
    ).casefold()
    return query in haystack


def _matches_choice(row: dict[str, Any], field: str, selected: tuple[str, ...]) -> bool:
    return not selected or str(row.get(field) or "") in selected


def _matches_overall_score(row: dict[str, Any], filters: RankingFilters) -> bool:
    score = clean_number(row.get("overall_score"))
    if score is None:
        return filters.include_unscored
    minimum, maximum = filters.overall_score_range
    return minimum <= score <= maximum


def _meets_minimum(value: Any, minimum: float) -> bool:
    if minimum <= 0:
        return True
    numeric = clean_number(value)
    return numeric is not None and numeric >= minimum
