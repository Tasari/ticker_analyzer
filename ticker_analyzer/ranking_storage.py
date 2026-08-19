from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_RANKING_PATH = Path("data/large_cap_ranking_v5.json")


def load_ranking(path: Path = DEFAULT_RANKING_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"metadata": {}, "companies": [], "errors": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_ranking(payload: dict[str, Any], path: Path = DEFAULT_RANKING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
