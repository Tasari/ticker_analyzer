from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yfinance as yf
from ticker_analyzer import analyze_ticker, load_config
from ticker_analyzer.scoring.robustness import audit_scoring_robustness, compact_analysis_result

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "META", "NVDA", "IBM", "CVX", "XOM", "T", "VZ",
    "F", "GM", "WBD", "PARA", "CCL", "SNOW", "PLTR", "SPCX", "JPM", "BAC",
    "GS", "FUTU", "AFRM", "SOFI", "CAT", "DE", "ARM", "CAVA",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure scoring stability after deterministic random removal of available metrics."
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="JSON list created with --sample-output; compact ranking snapshots are insufficient",
    )
    parser.add_argument("--live", action="store_true", help="Fetch and analyze a ticker sample")
    parser.add_argument(
        "--tickers",
        help="Comma-separated live ticker list; defaults to a broad industrial/financial sample",
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--rates", type=float, nargs="+", default=[0.10, 0.20])
    parser.add_argument("--seed", type=int, default=20260822)
    parser.add_argument("--output", type=Path, default=Path("robustness_report.json"))
    parser.add_argument(
        "--sample-output",
        type=Path,
        help="Optional metric-level JSON sample for repeatable offline audits",
    )
    args = parser.parse_args()
    if bool(args.input) == bool(args.live):
        parser.error("provide exactly one input JSON file or --live")
    if args.workers < 1:
        parser.error("--workers must be positive")

    config = load_config()
    failures: list[dict[str, str]] = []
    if args.live:
        tickers = _ticker_list(args.tickers)
        results, failures = _fetch_live(tickers, config, args.workers)
    else:
        results = _load_sample(args.input)
        tickers = [str(result.get("ticker") or "Unknown") for result in results]

    compact = [compact_analysis_result(result) for result in results]
    if args.sample_output:
        _write_json(args.sample_output, compact)
    report = audit_scoring_robustness(
        compact,
        config,
        dropout_rates=tuple(args.rates),
        trials=args.trials,
        seed=args.seed,
    )
    report["run"] = {
        "mode": "live" if args.live else "offline",
        "requested_tickers": tickers,
        "completed": len(compact),
        "failures": failures,
    }
    _write_json(args.output, report)
    print(json.dumps(_console_summary(report), indent=2))
    print(f"Full report: {args.output.resolve()}")
    if args.sample_output:
        print(f"Reusable sample: {args.sample_output.resolve()}")


def _fetch_live(
    tickers: list[str],
    config: dict[str, Any],
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    yf.set_tz_cache_location(str(Path(".yfinance_cache").resolve()))
    results_by_ticker: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(analyze_ticker, ticker, "3Y", config): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results_by_ticker[ticker] = future.result()
                print(f"Analyzed {ticker} ({len(results_by_ticker)}/{len(tickers)})")
            except Exception as exc:  # Provider failures belong in the audit run metadata.
                failures.append({"ticker": ticker, "error": str(exc)})
                print(f"Failed {ticker}: {exc}")
    return [results_by_ticker[ticker] for ticker in tickers if ticker in results_by_ticker], failures


def _load_sample(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        raise ValueError("An input path is required in offline mode.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("analyses"), list):
        return payload["analyses"]
    raise ValueError("Input must be a metric-level analysis list, not a ranking snapshot.")


def _ticker_list(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_TICKERS)
    tickers = list(dict.fromkeys(item.strip().upper() for item in raw.split(",") if item.strip()))
    if len(tickers) < 2:
        raise ValueError("At least two live tickers are required.")
    return tickers


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _console_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        rate: {
            segment: {
                "spearman_mean": values.get("spearman_mean"),
                "rating_flip_pct_mean": values.get("rating_flip_pct_mean"),
                "score_unavailable_pct_mean": values.get("score_unavailable_pct_mean"),
            }
            for segment, values in rate_report.get("segments", {}).items()
            if segment in {"All", "Industrial", "Financial"}
        }
        for rate, rate_report in report.get("dropout_rates", {}).items()
    }


if __name__ == "__main__":
    main()
