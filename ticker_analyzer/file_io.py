"""Atomic JSON writes shared by configuration and ranking persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any

_REPLACE_LOCK = Lock()


def write_json_atomic(payload: Any, path: Path, *, durable: bool = False) -> None:
    """Replace only a complete file; isolate temporary files for concurrent writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        # Windows can reject simultaneous replacements of the same destination.
        # Only publication is serialized; staging and JSON encoding remain parallel.
        with _REPLACE_LOCK:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
