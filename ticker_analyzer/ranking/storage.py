from __future__ import annotations

import json
import os
from functools import lru_cache
from itertools import chain
from pathlib import Path
from typing import Any

DEFAULT_RANKING_PATH = Path("data/large_cap_ranking_v5.json")
ETF_RANKING_PATH = Path("data/etf_ranking_v1.json")
CRYPTO_RANKING_PATH = Path("data/crypto_ranking_v1.json")
MAX_RANKING_IMPORT_BYTES = 50 * 1024 * 1024
MAX_RANKING_ROWS = 50_000


class RankingSnapshotError(ValueError):
    """Raised when an imported ranking snapshot is malformed or unsafe."""


def load_ranking(path: Path = DEFAULT_RANKING_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"metadata": {}, "companies": [], "errors": []}
    stat = path.stat()
    return _load_ranking_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _load_ranking_cached(resolved_path: str, modified_ns: int, size: int) -> dict[str, Any]:
    """Parse the current snapshot once and reuse it across Streamlit reruns."""
    del modified_ns, size  # Cache-key fields; reading only needs the resolved path.
    with Path(resolved_path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_ranking(payload: dict[str, Any], path: Path = DEFAULT_RANKING_PATH) -> None:
    validate_ranking_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        _load_ranking_cached.cache_clear()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def export_ranking(payload: dict[str, Any]) -> bytes:
    validate_ranking_payload(payload)
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def import_ranking(payload: bytes, path: Path = DEFAULT_RANKING_PATH) -> dict[str, Any]:
    if not payload:
        raise RankingSnapshotError("The uploaded ranking snapshot is empty.")
    if len(payload) > MAX_RANKING_IMPORT_BYTES:
        raise RankingSnapshotError("The uploaded ranking snapshot exceeds the 50 MB limit.")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RankingSnapshotError("The uploaded file is not valid UTF-8 JSON.") from exc
    validate_ranking_payload(parsed)
    save_ranking(parsed, path)
    return parsed


def validate_ranking_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise RankingSnapshotError("A ranking snapshot must be a JSON object.")
    required = {"metadata", "companies", "errors"}
    missing = required.difference(payload)
    if missing:
        raise RankingSnapshotError(f"Ranking snapshot is missing: {', '.join(sorted(missing))}.")
    metadata = payload["metadata"]
    companies = payload["companies"]
    errors = payload["errors"]
    universe = payload.get("universe", [])
    if not isinstance(metadata, dict):
        raise RankingSnapshotError("Ranking metadata must be a JSON object.")
    if not all(isinstance(collection, list) for collection in (companies, errors, universe)):
        raise RankingSnapshotError("Ranking companies, errors, and universe must be JSON arrays.")
    if len(companies) + len(errors) > MAX_RANKING_ROWS or len(universe) > MAX_RANKING_ROWS:
        raise RankingSnapshotError("Ranking snapshot contains too many rows.")
    if any(not isinstance(row, dict) for row in chain(companies, errors, universe)):
        raise RankingSnapshotError("Every ranking row must be a JSON object.")
    tickers = [str(row.get("ticker", "")).strip().upper() for row in companies]
    if any(not ticker for ticker in tickers):
        raise RankingSnapshotError("Every company row must contain a ticker.")
    if len(tickers) != len(set(tickers)):
        raise RankingSnapshotError("Ranking snapshot contains duplicate company tickers.")
