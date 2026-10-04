from __future__ import annotations

import json
import os
import tempfile
import unittest
import warnings
from io import BytesIO
from pathlib import Path
from stat import S_IFLNK
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from ticker_analyzer.ranking.bundle import (
    RANKING_SNAPSHOTS,
    available_ranking_snapshots,
    build_rankings_archive,
    import_rankings_archive,
    parse_rankings_archive,
)
from ticker_analyzer.ranking.storage import RankingSnapshotError, load_ranking, save_ranking


def snapshot(ticker, asset_class=None):
    return {"metadata": {"complete": True, **({"asset_class": asset_class} if asset_class else {})},
            "companies": [{"ticker": ticker, "name": ticker, "overall_score": 75}], "errors": []}


def archive_bytes(entries):
    buffer = BytesIO()
    with warnings.catch_warnings(), ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, payload in entries:
            archive.writestr(name, json.dumps(payload) if isinstance(payload, dict) else payload)
    return buffer.getvalue()


class RankingBundleTest(unittest.TestCase):
    def test_all_rankings_export_import_round_trip_and_invalidate_download_and_read_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {name: root / "source" / name for name in RANKING_SNAPSHOTS}
            targets = {name: root / "target" / name for name in RANKING_SNAPSHOTS}
            expected = {
                "stocks_ranking.json": snapshot("AAPL"),
                "etfs_ranking.json": snapshot("VVSM.DE", "ETF"),
                "crypto_ranking.json": snapshot("BTC-USD", "Crypto"),
            }
            for name, payload in expected.items():
                save_ranking(payload, sources[name])
                save_ranking(snapshot("OLD"), targets[name])
            old_archive = build_rankings_archive(targets)
            for path in targets.values():
                load_ranking(path)
            result = import_rankings_archive(build_rankings_archive(sources), targets)
            self.assertEqual(result, expected)
            self.assertEqual({name: load_ranking(path) for name, path in targets.items()}, expected)
            self.assertNotEqual(build_rankings_archive(targets), old_archive)
            self.assertEqual(set(root.joinpath("target").iterdir()), set(targets.values()))

    def test_partial_archive_preserves_other_saved_rankings(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory) / name for name in RANKING_SNAPSHOTS}
            for path in paths.values():
                save_ranking(snapshot("OLD"), path)
            original = {name: path.read_bytes() for name, path in paths.items()}
            imported = import_rankings_archive(archive_bytes([("etfs_ranking.json", snapshot("VVSM.DE", "ETF"))]), paths)
            self.assertEqual(set(imported), {"etfs_ranking.json"})
            self.assertEqual(load_ranking(paths["etfs_ranking.json"])["companies"][0]["ticker"], "VVSM.DE")
            for name in ("stocks_ranking.json", "crypto_ranking.json"):
                self.assertEqual(paths[name].read_bytes(), original[name])

    def test_invalid_second_snapshot_does_not_replace_any_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory) / name for name in RANKING_SNAPSHOTS}
            for path in paths.values():
                save_ranking(snapshot("OLD"), path)
            original = {name: path.read_bytes() for name, path in paths.items()}
            invalid = archive_bytes([("stocks_ranking.json", snapshot("AAPL")), ("etfs_ranking.json", {"companies": []})])
            with self.assertRaises(RankingSnapshotError):
                import_rankings_archive(invalid, paths)
            self.assertEqual({name: path.read_bytes() for name, path in paths.items()}, original)

    def test_failed_second_replacement_restores_existing_or_absent_first_snapshot(self):
        replace_file = os.replace
        for existing_stock in (True, False):
            with self.subTest(existing_stock=existing_stock), tempfile.TemporaryDirectory() as directory:
                paths = {name: Path(directory) / name for name in RANKING_SNAPSHOTS}
                if existing_stock:
                    save_ranking(snapshot("OLD"), paths["stocks_ranking.json"])
                save_ranking(snapshot("OLD-ETF"), paths["etfs_ranking.json"])
                original = {name: path.read_bytes() for name, path in paths.items() if path.exists()}

                def fail_etf(source, target, failed_path=paths["etfs_ranking.json"]):
                    if target == failed_path:
                        raise OSError("disk failure")
                    replace_file(source, target)

                payload = archive_bytes([("stocks_ranking.json", snapshot("AAPL")), ("etfs_ranking.json", snapshot("VOO", "ETF"))])
                with patch("ticker_analyzer.ranking.bundle.os.replace", side_effect=fail_etf), self.assertRaisesRegex(OSError, "disk failure"):
                    import_rankings_archive(payload, paths)
                self.assertEqual({name: path.read_bytes() for name, path in paths.items() if path.exists()}, original)

    def test_unknown_paths_duplicates_wrong_asset_classes_and_non_json_are_rejected(self):
        cases = [
            ([('../stocks_ranking.json', snapshot("AAPL"))], "only"),
            ([('/stocks_ranking.json', snapshot("AAPL"))], "only"),
            ([('stocks_ranking.json', snapshot("AAPL")), ('stocks_ranking.json', snapshot("MSFT"))], "duplicate"),
            ([('etfs_ranking.json', snapshot("BTC-USD", "Crypto"))], "asset class"),
            ([('stocks_ranking.json', b'not json')], "UTF-8 JSON"),
            ([('stocks_ranking.json', snapshot("AAPL", "ETF"))], "asset class"),
        ]
        for entries, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(RankingSnapshotError, message):
                parse_rankings_archive(archive_bytes(entries))

    def test_zip_and_expanded_snapshot_sizes_are_bounded(self):
        with patch("ticker_analyzer.ranking.bundle.MAX_RANKING_IMPORT_BYTES", 1024):
            with self.assertRaisesRegex(RankingSnapshotError, "ZIP exceeds"):
                parse_rankings_archive(b"x" * 1025)
            payload = snapshot("AAPL")
            payload["metadata"]["description"] = "x" * 2048
            with self.assertRaisesRegex(RankingSnapshotError, "uncompressed"):
                parse_rankings_archive(archive_bytes([("stocks_ranking.json", payload)]))

    def test_empty_corrupt_and_symlink_archives_are_rejected(self):
        for payload in (b"", b"not zip", archive_bytes([])):
            with self.subTest(payload=payload), self.assertRaises(RankingSnapshotError):
                parse_rankings_archive(payload)
        entry = ZipInfo("stocks_ranking.json")
        entry.create_system = 3
        entry.external_attr = S_IFLNK << 16
        with self.assertRaisesRegex(RankingSnapshotError, "symbolic links"):
            parse_rankings_archive(archive_bytes([(entry, snapshot("AAPL"))]))
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
