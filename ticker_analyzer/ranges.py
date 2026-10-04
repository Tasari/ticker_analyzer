"""Year-range parsing shared by analysis and market-data requests."""

from __future__ import annotations


def years_from_range(label: str) -> int:
    normalized = label.strip().lower()
    if normalized.endswith("y"):
        try:
            return max(1, int(normalized[:-1]))
        except ValueError:
            pass
    return 2
