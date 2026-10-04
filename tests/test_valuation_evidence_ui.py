from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class ValuationEvidenceUiTests(unittest.TestCase):
    def test_unrated_value_explains_missing_coverage(self):
        app = AppTest.from_string('''
from ticker_analyzer.ui.analysis_views import render_tab
render_tab("Value", {
    "score": None, "rating": "Not Rated", "metrics": [],
    "coverage": {"percentage": 47.7778},
    "group_breakdown": {"reason": "minimum_weight_coverage", "minimum_coverage": 50},
}, {})
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("47.8%" in item.value and "50.0%" in item.value for item in app.warning))

    def test_unrated_value_explains_required_cash_yield_even_with_high_coverage(self):
        app = AppTest.from_string('''
from tests.test_analysis_engine import FakeProvider, market_data
import pandas as pd
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config
from ticker_analyzer.ui.analysis_views import render_tab
data = market_data()
data.annual_cashflow = data.quarterly_cashflow = pd.DataFrame()
result = StockAnalysisEngine(provider=FakeProvider(data)).analyze("TEST", "2Y", load_config()).as_dict()
render_tab("Value", result["tabs"]["Value"], {})
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("absolute cash yield" in item.value for item in app.warning))

    def test_unrated_value_surfaces_unverified_share_basis_in_tab(self):
        app = AppTest.from_string('''
from tests.test_analysis_engine import FakeProvider, market_data
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config
from ticker_analyzer.ui.analysis_views import render_tabs
data = market_data(industry="Banks - Diversified")
data.ticker = "OTHER"
data.info["country"] = "Japan"
result = StockAnalysisEngine(provider=FakeProvider(data)).analyze("OTHER", "2Y", load_config()).as_dict()
render_tabs(result)
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Share/ADR conversion is unverified" in item.value for item in app.warning))

    def test_unrated_value_shows_download_failure_and_retry_action(self):
        app = AppTest.from_string('''
from tests.test_analysis_engine import FakeProvider, market_data
import pandas as pd
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config
from ticker_analyzer.ui.analysis_views import render_tabs
data = market_data()
data.annual_cashflow = data.quarterly_cashflow = pd.DataFrame()
data.diagnostics.append({"source": "annual cash flow", "kind": "provider_error", "message": "429 Too Many Requests"})
result = StockAnalysisEngine(provider=FakeProvider(data)).analyze("TEST", "2Y", load_config()).as_dict()
render_tabs(result)
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Click Analyze" in item.value for item in app.warning))
        self.assertTrue(any(item.label == "Data download failures" for item in app.expander))
        self.assertTrue(any("429 Too Many Requests" in item.value for item in app.text))

    def test_evidence_table_and_warning_are_rendered_from_engine_result(self):
        app = AppTest.from_string('''
from tests.test_analysis_engine import FakeProvider, market_data
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.config import load_config
from ticker_analyzer.ui.analysis_views import render_valuation_evidence
result = StockAnalysisEngine(provider=FakeProvider(market_data())).analyze("TEST", "2Y", load_config()).as_dict()
render_valuation_evidence(result)
''').run()
        self.assertFalse(app.exception)
        self.assertEqual(app.expander[0].label, "Valuation evidence")
        frame = app.dataframe[0].value
        self.assertEqual(list(frame["Metric"]), ["P/S", "P/E", "P/B", "EV/EBITDA"])
        self.assertTrue(frame.loc[frame["Metric"] == "P/E", "Statement period"].iloc[0].startswith("TTM"))
        self.assertTrue(any("25%" in item.value for item in app.warning))

    def test_legacy_analysis_without_evidence_still_renders(self):
        app = AppTest.from_string('''
from ticker_analyzer.ui.analysis_views import render_valuation_evidence
render_valuation_evidence({"raw": {"pe_current": {"value": 10.}}})
''').run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 0)

    def test_legacy_ranking_warns_without_preventing_viewing_it(self):
        app = AppTest.from_string('''
from ticker_analyzer.ui.ranking_view import _render_ranking_compatibility
_render_ranking_compatibility({"metadata": {"generated_at": "2020-01-01"}})
''').run()
        self.assertFalse(app.exception)
        self.assertTrue(any("Update all rankings" in item.value for item in app.warning))
        self.assertEqual(app.expander[0].label, "Ranking calculation versions")
        self.assertGreater(len(app.dataframe[0].value), 0)

    def test_current_ranking_reports_matching_configuration(self):
        app = AppTest.from_string('''
from ticker_analyzer.config import load_config
from ticker_analyzer.ranking.builder import analysis_fingerprint
from ticker_analyzer.ui.ranking_view import _render_ranking_compatibility
_render_ranking_compatibility({"metadata": analysis_fingerprint(load_config(), "2026-09-09")})
''').run()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertTrue(any("match" in item.value for item in app.success))
