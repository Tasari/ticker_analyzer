from __future__ import annotations

import argparse
import json
import shutil
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
    if version not in {3, 4}:
        raise ConfigValidationError(f"Unsupported source config version: {version}")
    migrated = normalize_config(payload)
    target = destination or source
    if target == source:
        backup = source.with_suffix(source.suffix + ".v3.bak")
        if version == 3 and not backup.exists():
            shutil.copy2(source, backup)
    target.write_text(json.dumps(migrated, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate a scoring config from v3 to validated v4.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    target = migrate_file(args.source, args.output)
    print(f"Saved scoring config v4 to {target}")


if __name__ == "__main__":
    main()
