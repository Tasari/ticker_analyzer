from __future__ import annotations

import unittest

import pandas as pd
from ticker_analyzer.metrics.valuation import known_facts_at, make_point_in_time, point_in_time_multiple


class PointInTimeTest(unittest.TestCase):
    def test_period_end_fact_is_not_known_before_conservative_filing_date(self):
        series = pd.Series([100.0], index=[pd.Timestamp("2025-12-31")])
        shifted = make_point_in_time(series)
        self.assertTrue(pd.isna(shifted.asof(pd.Timestamp("2026-02-01"))))
        self.assertEqual(shifted.asof(pd.Timestamp("2026-04-01")), 100.0)

    def test_known_facts_excludes_future_filings_and_keeps_latest_amendment(self):
        facts = pd.DataFrame(
            {
                "period_end": ["2025-12-31", "2025-12-31", "2026-03-31"],
                "filed_at": ["2026-02-01", "2026-02-10", "2026-05-01"],
                "value": [10, 11, 12],
            }
        )
        result = known_facts_at(facts, "2026-03-01")
        self.assertEqual(result["value"].tolist(), [11])

    def test_explicit_filing_date_replaces_conservative_default_lag(self):
        period = pd.Timestamp("2025-12-31")
        shifted = make_point_in_time(
            pd.Series([100.0], index=[period]),
            filing_dates={period: pd.Timestamp("2026-02-01")},
        )
        self.assertEqual(shifted.index[0], pd.Timestamp("2026-02-01"))

    def test_multiple_cannot_use_filing_before_publication(self):
        facts = pd.DataFrame(
            {"period_end": ["2025-12-31"], "filed_at": ["2026-02-15"], "revenue_ttm": [1000.0]}
        )
        self.assertIsNone(
            point_in_time_multiple(market_cap=5000, facts=facts, price_date="2026-02-01", field="revenue_ttm")
        )
        self.assertEqual(
            point_in_time_multiple(market_cap=5000, facts=facts, price_date="2026-02-20", field="revenue_ttm"),
            5.0,
        )


if __name__ == "__main__":
    unittest.main()
