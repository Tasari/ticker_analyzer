from __future__ import annotations

import unittest

from ticker_analyzer.analysis.fair_value import (
    FairValueAssumptions,
    FairValueInputs,
    FairValueScenario,
    calculate_fair_value,
    default_assumptions,
    inputs_from_analysis,
)


def scenario(name: str, **overrides: float) -> FairValueScenario:
    values = {
        "revenue_growth_percent": 0.0,
        "target_fcf_margin_percent": 20.0,
        "earnings_growth_percent": 0.0,
        "earnings_multiple": 10.0,
        "fcf_growth_percent": 0.0,
        "fcf_multiple": 10.0,
        "dividend_growth_percent": 3.0,
        **overrides,
    }
    return FairValueScenario(name=name, **values)


class FairValueTest(unittest.TestCase):
    def setUp(self):
        self.inputs = FairValueInputs(
            current_price=100,
            currency="USD",
            revenue_per_share=10,
            free_cash_flow_per_share=2,
            earnings_per_share=5,
            dividend_per_share=2,
            current_fcf_margin_percent=20,
        )
        self.assumptions = FairValueAssumptions(
            horizon_years=1,
            discount_rate_percent=10,
            terminal_growth_percent=2,
            scenarios=(scenario("Bear"), scenario("Base"), scenario("Bull")),
        )

    def test_all_methods_produce_transparent_per_share_estimates(self):
        result = calculate_fair_value(self.inputs, self.assumptions)
        base = {estimate.method: estimate for estimate in result.estimates if estimate.scenario == "Base"}

        self.assertAlmostEqual(base["DCF"].value, 25.0)
        self.assertAlmostEqual(base["Earnings multiple"].value, 50 / 1.1)
        self.assertAlmostEqual(base["FCF multiple"].value, 20 / 1.1)
        self.assertAlmostEqual(base["Dividend discount"].value, 2 * 1.03 / 0.07)
        self.assertAlmostEqual(result.base_value, (25 + 2 * 1.03 / 0.07) / 2)
        self.assertEqual(result.range_low, result.base_value)
        self.assertEqual(result.range_high, result.base_value)

    def test_bear_base_bull_consensus_forms_a_range_instead_of_one_target(self):
        assumptions = FairValueAssumptions(
            horizon_years=5,
            discount_rate_percent=10,
            terminal_growth_percent=2,
            scenarios=(
                scenario("Bear", revenue_growth_percent=0, target_fcf_margin_percent=15, earnings_multiple=8),
                scenario("Base", revenue_growth_percent=5, target_fcf_margin_percent=20, earnings_multiple=12),
                scenario("Bull", revenue_growth_percent=10, target_fcf_margin_percent=25, earnings_multiple=16),
            ),
        )

        result = calculate_fair_value(self.inputs, assumptions)

        self.assertLess(result.consensus["Bear"], result.consensus["Base"])
        self.assertLess(result.consensus["Base"], result.consensus["Bull"])
        self.assertEqual(result.range_low, result.consensus["Bear"])
        self.assertEqual(result.range_high, result.consensus["Bull"])

    def test_missing_or_non_positive_inputs_disable_only_affected_methods(self):
        inputs = FairValueInputs(
            current_price=100,
            currency="USD",
            revenue_per_share=None,
            free_cash_flow_per_share=-1,
            earnings_per_share=5,
            dividend_per_share=None,
            current_fcf_margin_percent=None,
        )

        result = calculate_fair_value(inputs, self.assumptions)
        base = {estimate.method: estimate for estimate in result.estimates if estimate.scenario == "Base"}

        self.assertIsNone(base["DCF"].value)
        self.assertIsNotNone(base["Earnings multiple"].value)
        self.assertIsNone(base["FCF multiple"].value)
        self.assertIsNone(base["Dividend discount"].value)
        self.assertEqual(result.base_value, base["Earnings multiple"].value)

    def test_inputs_are_reconstructed_in_quote_currency_from_analyzer_ratios(self):
        inputs = inputs_from_analysis(
            {
                "current_price": 100,
                "currency": "USD",
                "raw": {
                    "pe_current": {"value": 20},
                    "price_to_sales_current": {"value": 5},
                    "fcf_yield_ttm": {"value": 8},
                    "fcf_margin": {"value": 15},
                    "fair_value_dividend_per_share": {"value": 2},
                },
            }
        )

        self.assertEqual(inputs.earnings_per_share, 5)
        self.assertEqual(inputs.revenue_per_share, 20)
        self.assertEqual(inputs.free_cash_flow_per_share, 8)
        self.assertEqual(inputs.current_fcf_margin_percent, 40)
        self.assertEqual(inputs.dividend_per_share, 2)

    def test_defaults_use_forward_growth_and_keep_financial_multiples_conservative(self):
        assumptions = default_assumptions(
            {
                "current_price": 100,
                "profile": "FinancialBroker",
                "raw": {
                    "revenue_estimate_growth": {"value": 12},
                    "eps_estimate_avg_growth": {"value": 18},
                    "cfo_range_growth": {"value": 9},
                    "fcf_margin": {"value": 20},
                    "pe_current": {"value": 30},
                },
            }
        )

        self.assertEqual([item.name for item in assumptions.scenarios], ["Bear", "Base", "Bull"])
        self.assertEqual(assumptions.scenarios[1].revenue_growth_percent, 12)
        self.assertEqual(assumptions.scenarios[1].earnings_multiple, 18)
        self.assertLess(assumptions.scenarios[0].earnings_multiple, assumptions.scenarios[1].earnings_multiple)

    def test_invalid_terminal_growth_disables_only_dcf(self):
        invalid_terminal = FairValueAssumptions(
            horizon_years=5,
            discount_rate_percent=5,
            terminal_growth_percent=5,
            scenarios=self.assumptions.scenarios,
        )
        result = calculate_fair_value(self.inputs, invalid_terminal)
        base = {estimate.method: estimate for estimate in result.estimates if estimate.scenario == "Base"}
        self.assertIsNone(base["DCF"].value)
        self.assertIsNotNone(base["Earnings multiple"].value)

    def test_invalid_scenario_set_is_rejected(self):
        invalid_scenarios = FairValueAssumptions(
            horizon_years=5,
            discount_rate_percent=10,
            terminal_growth_percent=2,
            scenarios=(scenario("Base"),),
        )
        with self.assertRaisesRegex(ValueError, "Bear, Base and Bull"):
            calculate_fair_value(self.inputs, invalid_scenarios)


if __name__ == "__main__":
    unittest.main()
