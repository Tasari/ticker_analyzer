from __future__ import annotations

from importlib import import_module
from typing import Any


def resolve_export(
    name: str,
    exports: dict[str, str],
    namespace: dict[str, Any],
    module_name: str,
) -> Any:
    target_module = exports.get(name)
    if target_module is None:
        raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
    value = getattr(import_module(target_module), name)
    namespace[name] = value
    return value
