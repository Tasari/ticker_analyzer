from __future__ import annotations

import argparse
from pathlib import Path

import yfinance as yf
from ticker_analyzer import load_config
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.ranking import (
    DEFAULT_RANKING_PATH,
    build_large_cap_ranking,
    checkpoint_universe_is_current,
    fetch_large_cap_universe,
    fetch_large_cap_universe_nasdaq,
    load_ranking,
    merge_large_cap_universes,
    normalize_ticker,
    save_ranking,
)
from ticker_analyzer.ranking_provider import PublicYahooRankingProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a resumable scoring-v5 ranking of large US equities.")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--ranges", default="3Y")
    parser.add_argument("--data-as-of", help="UTC snapshot date (YYYY-MM-DD); defaults to today.")
    parser.add_argument("--output", type=Path, default=DEFAULT_RANKING_PATH)
    parser.add_argument("--restart", action="store_true", help="Ignore a previous checkpoint.")
    parser.add_argument("--public-fallback", action="store_true", help="Use public Yahoo timeseries endpoints instead of crumb-based yfinance.")
    parser.add_argument("--retry-insufficient", action="store_true", help="Recalculate rows that previously lacked an Overall score.")
    args = parser.parse_args()

    yf.set_tz_cache_location(str(Path(".yfinance_cache").resolve()))
    previous = None if args.restart else load_ranking(args.output)
    saved_universe = (previous or {}).get("universe", [])
    for item in saved_universe:
        item["ticker"] = normalize_ticker(item.get("ticker"))
    # Preserve the exact universe only while resuming an incomplete checkpoint.
    # A new run from a complete snapshot must fetch it again so newly eligible
    # listings and ADRs can enter the ranking.
    if checkpoint_universe_is_current(previous, limit=args.limit):
        universe = saved_universe[: args.limit]
    else:
        yahoo_universe = []
        nasdaq_universe = []
        try:
            yahoo_universe = fetch_large_cap_universe(args.limit)
        except Exception as exc:
            print(f"Yahoo universe unavailable ({exc}).")
        try:
            # Unlike Yahoo's region filter, this includes foreign companies and
            # ADRs listed in the US (for example FUTU).
            nasdaq_universe = fetch_large_cap_universe_nasdaq(args.limit)
        except Exception as exc:
            print(f"Nasdaq universe unavailable ({exc}).")
        universe = merge_large_cap_universes(nasdaq_universe, yahoo_universe, limit=args.limit)
        if not universe:
            raise RuntimeError("No ranking universe could be fetched from Yahoo or Nasdaq.")
        seed = previous or {"companies": [], "errors": []}
        seed["universe"] = universe
        save_ranking(seed, args.output)
    checkpoint = lambda payload: save_ranking(payload, args.output)  # noqa: E731
    analyzer = None
    if args.public_fallback:
        provider = PublicYahooRankingProvider({item["ticker"]: item for item in universe})
        engine = StockAnalysisEngine(provider=provider)
        analyzer = lambda ticker, ranges, config: engine.analyze(ticker, ranges, config).as_dict()  # noqa: E731
    build_kwargs = {"analyzer": analyzer} if analyzer else {}
    result = build_large_cap_ranking(
        universe,
        load_config(),
        ranges=args.ranges,
        workers=args.workers,
        existing=previous,
        checkpoint=checkpoint,
        retries=1,
        retry_insufficient=args.retry_insufficient,
        data_as_of=args.data_as_of,
        **build_kwargs,
    )
    save_ranking(result, args.output)
    print(result["metadata"])


if __name__ == "__main__":
    main()
