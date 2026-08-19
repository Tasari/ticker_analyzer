from __future__ import annotations

from pathlib import Path
from typing import Any


def analyze_ticker(
    ticker_symbol: str,
    ranges: str | dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    from ticker_analyzer.analysis.engine import analyze_ticker as _analyze_ticker

    return _analyze_ticker(ticker_symbol, ranges, config)


def format_metric_value(value: float | None, unit: str) -> str:
    from ticker_analyzer.scoring import format_metric_value as _format_metric_value

    return _format_metric_value(value, unit)


def load_config(path: Path | None = None) -> dict[str, Any]:
    from ticker_analyzer.config import CONFIG_PATH, load_config as _load_config

    return _load_config(CONFIG_PATH if path is None else path)


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    from ticker_analyzer.config import CONFIG_PATH, save_config as _save_config

    _save_config(config, CONFIG_PATH if path is None else path)


__all__ = ["analyze_ticker", "format_metric_value", "load_config", "save_config"]
