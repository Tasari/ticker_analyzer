from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ticker_analyzer.config import ConfigValidationError, normalize_config


def migrate_file(source: Path, destination: Path | None = None) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    version = int(payload.get("version", 0))
    if version == 2:
        raise ConfigValidationError(
            "Config v2 cannot be migrated automatically because warn/good semantics changed; "
            "review each anchor and first save an explicit v3 config."
        )
    if version not in {3, 4, 5}:
        raise ConfigValidationError(f"Unsupported source config version: {version}")
    migrated = normalize_config(payload)
    target = destination or source
    if target == source:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = source.with_suffix(source.suffix + f".v{version}.{timestamp}.bak")
        shutil.copy2(source, backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(migrated, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a scoring config from v3/v4 to validated v5.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = migrate_file(args.source, args.output)
    print(f"Saved scoring config v5 to {target}")


if __name__ == "__main__":
    main()
