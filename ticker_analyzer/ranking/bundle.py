from __future__ import annotations

import os
import shutil
import zlib
from contextlib import ExitStack
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from stat import S_ISLNK, S_ISREG
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, LargeZipFile, ZipFile

from ticker_analyzer.ranking.storage import (
    CRYPTO_RANKING_PATH,
    DEFAULT_RANKING_PATH,
    ETF_RANKING_PATH,
    MAX_RANKING_IMPORT_BYTES,
    RankingSnapshotError,
    _load_ranking_cached,
    export_ranking,
    parse_ranking_snapshot,
)

RANKING_SNAPSHOTS = {
    "stocks_ranking.json": DEFAULT_RANKING_PATH,
    "etfs_ranking.json": ETF_RANKING_PATH,
    "crypto_ranking.json": CRYPTO_RANKING_PATH,
}
RANKING_LABELS = {"stocks_ranking.json": "Stocks", "etfs_ranking.json": "ETFs", "crypto_ranking.json": "Crypto"}


def parse_rankings_archive(payload: bytes) -> dict[str, dict[str, Any]]:
    """Read only the known ZIP entries in memory; never extract uploaded paths."""
    if not payload:
        raise RankingSnapshotError("The uploaded ranking ZIP is empty.")
    if len(payload) > MAX_RANKING_IMPORT_BYTES:
        raise RankingSnapshotError("The uploaded ranking ZIP exceeds the 50 MB limit.")
    try:
        with ZipFile(BytesIO(payload)) as archive:
            entries = archive.infolist()
            if not entries:
                raise RankingSnapshotError("The ZIP contains no ranking snapshots.")
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)):
                raise RankingSnapshotError("The ZIP contains duplicate snapshot names.")
            if any(name not in RANKING_SNAPSHOTS for name in names):
                raise RankingSnapshotError("The ZIP may contain only stocks_ranking.json, etfs_ranking.json and crypto_ranking.json. Use Download all rankings to create it.")
            if any(entry.file_size > MAX_RANKING_IMPORT_BYTES for entry in entries):
                raise RankingSnapshotError("Each uncompressed ranking snapshot must be at most 50 MB.")
            if any(entry.flag_bits & 1 or S_ISLNK(entry.external_attr >> 16) for entry in entries):
                raise RankingSnapshotError("Encrypted entries and symbolic links are not supported.")
            imported = {}
            for entry in entries:
                with archive.open(entry) as member:
                    snapshot = parse_ranking_snapshot(member.read(MAX_RANKING_IMPORT_BYTES + 1))
                declared_type = snapshot["metadata"].get("asset_class")
                allowed_types = {
                    "stocks_ranking.json": {"stock", "stocks", "equity", "equities"},
                    "etfs_ranking.json": {"etf", "etfs"},
                    "crypto_ranking.json": {"crypto", "cryptocurrency", "cryptocurrencies"},
                }
                if declared_type is not None and str(declared_type).casefold() not in allowed_types[entry.filename]:
                    raise RankingSnapshotError(f"{entry.filename} declares the wrong asset class.")
                imported[entry.filename] = snapshot
            return imported
    except (BadZipFile, LargeZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
        raise RankingSnapshotError("The uploaded file is not a readable, intact ranking ZIP.") from exc


def import_rankings_archive(
    payload: bytes, paths: dict[str, Path] = RANKING_SNAPSHOTS,
) -> dict[str, dict[str, Any]]:
    """Validate all snapshots first, stage all writes, and roll back a failed replacement."""
    imported = parse_rankings_archive(payload)
    staged = {}
    backups = {}
    replaced = []
    with ExitStack() as stack:
        for name, snapshot in imported.items():
            path = paths[name]
            path.parent.mkdir(parents=True, exist_ok=True)
            directory = Path(stack.enter_context(TemporaryDirectory(prefix="ranking-import-", dir=path.parent)))
            staged[name] = directory / "new.json"
            staged[name].write_bytes(export_ranking(snapshot))
            if path.exists():
                backups[name] = directory / "previous.json"
                shutil.copy2(path, backups[name])
        try:
            for name in imported:
                os.replace(staged[name], paths[name])
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                if name in backups:
                    os.replace(backups[name], paths[name])
                else:
                    paths[name].unlink(missing_ok=True)
            raise
        finally:
            _load_ranking_cached.cache_clear()
            _build_rankings_archive_cached.cache_clear()
    return imported


def available_ranking_snapshots(paths: dict[str, Path] = RANKING_SNAPSHOTS) -> int:
    return len(_ranking_signature(paths))


def build_rankings_archive(paths: dict[str, Path] = RANKING_SNAPSHOTS) -> bytes:
    return _build_rankings_archive_cached(_ranking_signature(paths))


def _ranking_signature(paths: dict[str, Path]) -> tuple[tuple[str, str, int, int], ...]:
    signature = []
    for archive_name, path in paths.items():
        try:
            file_stat = path.stat()
        except OSError:
            continue
        if S_ISREG(file_stat.st_mode):
            signature.append((archive_name, str(path.resolve()), file_stat.st_mtime_ns, file_stat.st_size))
    return tuple(signature)


@lru_cache(maxsize=4)
def _build_rankings_archive_cached(signature: tuple[tuple[str, str, int, int], ...]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for archive_name, resolved_path, _modified, _size in signature:
            archive.write(resolved_path, arcname=archive_name)
    return buffer.getvalue()
