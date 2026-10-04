"""Shared display formats; percentage values here are fractional returns."""

from __future__ import annotations


def money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def ratio(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}"
