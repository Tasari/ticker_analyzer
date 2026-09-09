from __future__ import annotations

import unittest

from streamlit.testing.v1 import AppTest


class ValuationEvidenceUiTests(unittest.TestCase):
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
