"""Lightweight calculation fingerprints shared by the scanner and snapshot UI."""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCORING_VERSION = 5
PROVIDER_SCHEMA_VERSION = "providers-v4-fx-adr"
METRIC_SCHEMA_VERSION = "metrics-v7-ev-evidence"


def config_digest(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_fingerprint(config: dict[str, Any], data_as_of: str) -> dict[str, Any]:
    return {
        "scoring_version": SCORING_VERSION,
        "config_version": int(config.get("version", 5)),
        "calibration_version": str(config.get("calibration_version", "v5.2-value-2026Q3")),
        "config_digest": config_digest(config),
        "provider_schema_version": PROVIDER_SCHEMA_VERSION,
        "metric_schema_version": METRIC_SCHEMA_VERSION,
        "peer_artifact_version": str(config.get("peer_artifact_version", "none")),
        "data_as_of": data_as_of,
    }
