from __future__ import annotations

import hashlib
import json
from typing import Any


def analysis_cache_key(ticker: str, ranges: str | dict[str, str], config: dict[str, Any]) -> str:
    """Version-safe cache key for analyses and resumable batch artifacts."""
    payload = {
        "ticker": ticker.strip().upper(),
        "ranges": ranges,
        "scoring_version": 5,
        "config_version": config.get("version", 5),
        "calibration_version": config.get("calibration_version"),
        "config": config,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
