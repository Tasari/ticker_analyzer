from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from ticker_analyzer.ranking_storage import DEFAULT_RANKING_PATH

ETF_RANKING_PATH = Path("data/etf_ranking_v1.json")
CRYPTO_RANKING_PATH = Path("data/crypto_ranking_v1.json")
RANKING_SNAPSHOTS = {
    "stocks_ranking.json": DEFAULT_RANKING_PATH,
    "etfs_ranking.json": ETF_RANKING_PATH,
    "crypto_ranking.json": CRYPTO_RANKING_PATH,
}


def available_ranking_snapshots(paths: dict[str, Path] = RANKING_SNAPSHOTS) -> int:
    return sum(path.is_file() for path in paths.values())


def build_rankings_archive(paths: dict[str, Path] = RANKING_SNAPSHOTS) -> bytes:
    signature = tuple(
        (archive_name, str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
        for archive_name, path in paths.items()
        if path.is_file()
    )
    return _build_rankings_archive_cached(signature)


@lru_cache(maxsize=4)
def _build_rankings_archive_cached(signature: tuple[tuple[str, str, int, int], ...]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for archive_name, resolved_path, _modified, _size in signature:
            archive.write(resolved_path, arcname=archive_name)
    return buffer.getvalue()
