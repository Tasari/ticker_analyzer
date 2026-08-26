from __future__ import annotations

import unittest

from ticker_analyzer.ranking.filters import RankingFilters, filter_ranking_companies


class RankingFiltersTest(unittest.TestCase):
    def setUp(self):
        self.companies = [
            {
                "ticker": "AAA",
                "company_name": "Alpha Bank",
                "industry": "Regional Banks",
                "country": "Poland",
                "market": "Poland",
                "exchange": "WSE",
                "sector": "Finance",
                "profile": "FinancialBroker",
                "rating": "Strong Buy",
                "rating_confidence": "High",
                "overall_score": 92,
                "growth_score": 80,
                "fundamentals_score": 90,
                "value_score": 95,
                "data_quality": 85,
                "market_cap": 20_000_000_000,
            },
            {
                "ticker": "BBB",
                "company_name": "Beta Software",
                "country": "United States",
                "market": "United States",
                "exchange": "NASDAQ",
                "sector": "Technology",
                "profile": "Industrial",
                "rating": "Buy",
                "rating_confidence": "Medium",
                "overall_score": 70,
                "growth_score": 95,
                "fundamentals_score": 60,
                "value_score": 50,
                "data_quality": 75,
                "market_cap": 5_000_000_000,
            },
            {"ticker": "EMPTY", "company_name": "Unscored", "overall_score": None},
        ]

    def test_combines_text_choices_scores_quality_and_market_cap(self):
        filters = RankingFilters(
            query="bank",
            countries=("Poland",),
            markets=("Poland",),
            exchanges=("WSE",),
            sectors=("Finance",),
            profiles=("FinancialBroker",),
            ratings=("Strong Buy",),
            confidences=("High",),
            overall_score_range=(90, 100),
            minimum_growth=75,
            minimum_fundamentals=85,
            minimum_value=90,
            minimum_quality=80,
            minimum_market_cap=10_000_000_000,
        )

        result = filter_ranking_companies(self.companies, filters)

        self.assertEqual([row["ticker"] for row in result], ["AAA"])

    def test_empty_choices_keep_all_rows_and_unscored_is_configurable(self):
        self.assertEqual(len(filter_ranking_companies(self.companies, RankingFilters())), 3)
        filtered = filter_ranking_companies(
            self.companies,
            RankingFilters(include_unscored=False),
        )
        self.assertEqual([row["ticker"] for row in filtered], ["AAA", "BBB"])

    def test_missing_and_invalid_numeric_values_fail_only_active_minimum_filters(self):
        companies = [
            {"ticker": "MISSING", "overall_score": "bad", "confidence": "bad"},
            {"ticker": "LEGACY", "overall_score": 80, "confidence": 90},
        ]

        filtered = filter_ranking_companies(
            companies,
            RankingFilters(minimum_quality=80, include_unscored=True),
        )

        self.assertEqual([row["ticker"] for row in filtered], ["LEGACY"])


if __name__ == "__main__":
    unittest.main()
