from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

import pandas as pd
import yfinance as yf
from ticker_analyzer import analyze_ticker, load_config

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "META", "NVDA", "IBM", "CVX", "XOM", "T", "VZ",
    "F", "GM", "WBD", "PARA", "CCL", "SNOW", "PLTR", "SPCX", "JPM", "BAC",
    "GS", "FUTU", "AFRM", "SOFI", "CAT", "DE", "ARM", "CAVA",
]


def describe(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}

    def percentile(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "count": len(ordered),
        "mean": mean(ordered),
        "median": median(ordered),
        "std": pstdev(ordered),
        "p05": percentile(0.05),
        "p25": percentile(0.25),
        "p50": percentile(0.50),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
        "min": ordered[0],
        "max": ordered[-1],
        "percentage_gte_90": sum(value >= 90 for value in ordered) / len(ordered) * 100,
        "percentage_gte_95": sum(value >= 95 for value in ordered) / len(ordered) * 100,
        "percentage_eq_100": sum(value == 100 for value in ordered) / len(ordered) * 100,
    }


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    series: dict[str, list[float]] = defaultdict(list)
    profile_series: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    missing_by_metric: dict[str, list[bool]] = defaultdict(list)
    exceptional: list[dict[str, Any]] = []
    correlation_rows: list[dict[str, float]] = []
    for result in results:
        profile = str(result.get("profile") or "Unknown")
        correlation_row: dict[str, float] = {}
        for tab in ("Growth", "Fundamentals", "Value"):
            score = result.get("tabs", {}).get(tab, {}).get("score")
            if score is not None:
                series[tab].append(float(score))
                profile_series[profile][tab].append(float(score))
                correlation_row[tab] = float(score)
            for metric in result.get("tabs", {}).get(tab, {}).get("metrics", []):
                metric_id = metric.get("id") if isinstance(metric, dict) else getattr(metric, "id", None)
                metric_score = metric.get("score") if isinstance(metric, dict) else getattr(metric, "score", None)
                if metric_id:
                    missing_by_metric[f"{tab}.{metric_id}"].append(metric_score is None)
        quality = result.get("data_quality", result.get("confidence"))
        for name, value in (("Overall", result.get("overall_score")), ("Data Quality", quality)):
            if value is not None:
                series[name].append(float(value))
                profile_series[profile][name].append(float(value))
                correlation_row[name] = float(value)
        if len(correlation_row) >= 2:
            correlation_rows.append(correlation_row)
        if result.get("rating") == "Strong Buy" or (result.get("overall_score") or 0) >= 90 or (quality or 0) >= 90:
            exceptional.append(
                {
                    "ticker": result.get("ticker"),
                    "profile": profile,
                    "overall_score": result.get("overall_score"),
                    "data_quality": quality,
                    "rating": result.get("rating"),
                }
            )
    correlations = pd.DataFrame(correlation_rows).corr(min_periods=3).round(4).to_dict() if correlation_rows else {}
    return {
        "model": {"scoring_version": 5, "calibration_version": "v5-audit-2026Q3"},
        "distributions": {name: describe(values) for name, values in series.items()},
        "profile_distributions": {
            profile: {name: describe(values) for name, values in values_by_name.items()}
            for profile, values_by_name in profile_series.items()
        },
        "correlations": correlations,
        "metric_missingness": {
            metric_id: sum(flags) / len(flags) * 100 for metric_id, flags in sorted(missing_by_metric.items())
        },
        "exceptional_cases": exceptional,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a deterministic scoring-v5 calibration report from exported JSON results.")
    parser.add_argument("input", type=Path, nargs="?", help="JSON file containing a list of analysis API results")
    parser.add_argument("--output", type=Path, default=Path("calibration_report_v5.json"))
    parser.add_argument("--live", action="store_true", help="Fetch the built-in broad ticker sample using yfinance")
    args = parser.parse_args()
    if args.live:
        yf.set_tz_cache_location(str(Path(".yfinance_cache").resolve()))
        config = load_config()
        results_by_ticker = {}
        failures = []

        def analyze(ticker: str) -> dict[str, Any]:
            return analyze_ticker(ticker, "3Y", config)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(analyze, ticker): ticker for ticker in DEFAULT_TICKERS}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    results_by_ticker[ticker] = future.result()
                except Exception as exc:  # A calibration run must report provider failures and continue.
                    failures.append({"ticker": ticker, "error": str(exc)})
        results = [results_by_ticker[ticker] for ticker in DEFAULT_TICKERS if ticker in results_by_ticker]
    elif args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "companies" in payload:
            results = [ranking_row_as_analysis(row) for row in payload.get("companies", [])]
        elif isinstance(payload, list):
            results = payload
        else:
            raise ValueError("Input must be a list of analyses or a ranking snapshot.")
        failures = []
    else:
        parser.error("provide an input JSON file or use --live")
    report = build_report(results)
    report["sample"] = {"requested": len(DEFAULT_TICKERS) if args.live else len(results), "completed": len(results), "failures": failures}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def ranking_row_as_analysis(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["tabs"] = {
        tab: {"score": row.get(f"{tab.lower()}_score"), "metrics": []}
        for tab in ("Growth", "Fundamentals", "Value")
    }
    result["data_quality"] = row.get("data_quality", row.get("confidence"))
    return result


if __name__ == "__main__":
    main()
