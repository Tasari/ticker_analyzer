from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from ticker_analyzer.ranking_bundle import available_ranking_snapshots, build_rankings_archive


class RankingBundleTest(unittest.TestCase):
    def test_archive_contains_every_available_ranking_and_skips_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stocks = root / "stocks.json"
            etfs = root / "etfs.json"
            stocks.write_text('{"companies":[{"ticker":"AAPL"}]}', encoding="utf-8")
            etfs.write_text('{"companies":[{"ticker":"CSPX.L"}]}', encoding="utf-8")
            paths = {
                "stocks_ranking.json": stocks,
                "etfs_ranking.json": etfs,
                "crypto_ranking.json": root / "missing.json",
            }

            self.assertEqual(available_ranking_snapshots(paths), 2)
            with ZipFile(BytesIO(build_rankings_archive(paths))) as archive:
                self.assertEqual(set(archive.namelist()), {"stocks_ranking.json", "etfs_ranking.json"})
                self.assertIn("AAPL", archive.read("stocks_ranking.json").decode("utf-8"))
                self.assertIn("CSPX.L", archive.read("etfs_ranking.json").decode("utf-8"))

    def test_empty_archive_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {"missing.json": Path(directory) / "missing.json"}
            with ZipFile(BytesIO(build_rankings_archive(paths))) as archive:
                self.assertEqual(archive.namelist(), [])


if __name__ == "__main__":
    unittest.main()
