from __future__ import annotations

import textwrap
import unittest

from streamlit.testing.v1 import AppTest


class FairValueViewTest(unittest.TestCase):
    def test_complete_inputs_render_range_methods_and_chart(self):
        app = AppTest.from_string(
            textwrap.dedent(
                """
                from ticker_analyzer.ui.fair_value_view import render_fair_value

                render_fair_value({
                    "ticker": "TEST",
                    "current_price": 100,
                    "currency": "USD",
                    "profile": "Industrial",
                    "raw": {
                        "pe_current": {"value": 20},
                        "price_to_sales_current": {"value": 5},
                        "fcf_yield_ttm": {"value": 8},
                        "fcf_margin": {"value": 15},
                        "revenue_estimate_growth": {"value": 8},
                        "eps_estimate_avg_growth": {"value": 10},
                        "cfo_range_growth": {"value": 7},
                        "fair_value_eps": {"value": 5},
                        "fair_value_dividend_per_share": {"value": 2},
                    },
                })
                """
            )
        ).run()

        self.assertFalse(app.exception)
        self.assertTrue(any(metric.label == "Estimated range" and metric.value != "N/A" for metric in app.metric))
        self.assertTrue(any("Valuation methods" in item.value for item in app.markdown))


if __name__ == "__main__":
    unittest.main()
