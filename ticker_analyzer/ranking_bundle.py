from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from stat import S_ISREG
from zipfile import ZIP_DEFLATED, ZipFile

from ticker_analyzer.ranking_storage import CRYPTO_RANKING_PATH, DEFAULT_RANKING_PATH, ETF_RANKING_PATH

RANKING_SNAPSHOTS = {
    "stocks_ranking.json": DEFAULT_RANKING_PATH,
    "etfs_ranking.json": ETF_RANKING_PATH,
    "crypto_ranking.json": CRYPTO_RANKING_PATH,
}


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
