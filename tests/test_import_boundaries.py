import subprocess
import sys
import unittest


class ImportBoundaryTest(unittest.TestCase):
    def imported_modules(self, statement: str) -> set[str]:
        script = (
            f"{statement}\n"
            "import sys\n"
            "print('\\n'.join(sorted(sys.modules)))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return set(completed.stdout.splitlines())

    def test_package_import_does_not_load_analysis_stack(self):
        modules = self.imported_modules("import ticker_analyzer")

        self.assertNotIn("pandas", modules)
        self.assertNotIn("ticker_analyzer.analysis.engine", modules)
        self.assertNotIn("ticker_analyzer.scoring", modules)

    def test_ui_facade_does_not_eagerly_load_plotting_stack(self):
        modules = self.imported_modules("import ticker_analyzer.ui.views")

        self.assertNotIn("pandas", modules)
        self.assertNotIn("plotly", modules)
        self.assertNotIn("ticker_analyzer.ui.analysis_views", modules)

    def test_ranking_view_does_not_load_analysis_engine(self):
        modules = self.imported_modules(
            "from ticker_analyzer.ui import views\n"
            "views.render_large_cap_ranking"
        )

        self.assertNotIn("ticker_analyzer.analysis.engine", modules)
        self.assertNotIn("ticker_analyzer.ui.analysis_views", modules)
        self.assertNotIn("ticker_analyzer.ranking_builder", modules)
        self.assertNotIn("ticker_analyzer.ranking_universe", modules)
        self.assertNotIn("yfinance", modules)


if __name__ == "__main__":
    unittest.main()
