"""Analysis entry points, loaded lazily to keep metric imports independent."""

from importlib import import_module

__all__ = ["StockAnalysisEngine", "analyze_ticker"]


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    return getattr(import_module("ticker_analyzer.analysis.engine"), name)
