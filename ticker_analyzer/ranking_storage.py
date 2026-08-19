from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_RANKING_PATH = Path("data/large_cap_ranking_v5.json")


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
