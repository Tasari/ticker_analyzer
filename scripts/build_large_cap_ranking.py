from __future__ import annotations

import argparse
import time
from pathlib import Path

import yfinance as yf
from ticker_analyzer import load_config
from ticker_analyzer.analysis.engine import StockAnalysisEngine
from ticker_analyzer.ranking import (
    CHINA_ADR_MARKET,
    DEFAULT_RANKING_PATH,
    US_MARKET,
    XTB_EUROPE_MARKETS,
    build_large_cap_ranking,
    checkpoint_universe_is_current,
    combine_market_universes,
    fetch_large_cap_universe,
    fetch_large_cap_universe_with_retry,
    fetch_large_cap_universe_nasdaq,
    load_ranking,
    normalize_ticker,
    save_ranking,
    select_nasdaq_market,
    validate_market_coverage,
)
from ticker_analyzer.ranking_provider import PublicYahooRankingProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a resumable scoring-v5 multi-market ranking.")
    parser.add_argument("--limit", type=int, default=1000, help="US company quota.")
    parser.add_argument("--market-limit", type=int, default=100, help="Quota for each non-US market.")
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
    required_markets = [CHINA_ADR_MARKET, *XTB_EUROPE_MARKETS]
    if checkpoint_universe_is_current(
        previous,
        limit=args.limit,
        required_markets=required_markets,
    ):
        universe = saved_universe
    else:
        nasdaq_all = []
        try:
            nasdaq_all = fetch_large_cap_universe_nasdaq(10000)
        except Exception as exc:
            print(f"Nasdaq universe unavailable ({exc}).")
        us_nasdaq = select_nasdaq_market(
            nasdaq_all, country=US_MARKET, market=US_MARKET, limit=args.limit
        )
        china_adrs = select_nasdaq_market(
            nasdaq_all, country="China", market=CHINA_ADR_MARKET, limit=args.market_limit
        )
        yahoo_us = []
        try:
            yahoo_us = fetch_large_cap_universe(args.limit)
        except Exception as exc:
            print(f"Yahoo US universe unavailable ({exc}).")

        # yfinance uses shared Yahoo cookie/crumb state. Concurrent screener
        # calls can invalidate it and silently leave every European bucket empty.
        regional_by_name: dict[str, list[dict]] = {}
        regional_errors: list[str] = []
        for market, (region, country) in XTB_EUROPE_MARKETS.items():
            try:
                regional_by_name[market] = fetch_large_cap_universe_with_retry(
                    args.market_limit,
                    0,
                    region=region,
                    country=country,
                    market=market,
                )
            except Exception as exc:
                regional_by_name[market] = []
                regional_errors.append(str(exc))
            time.sleep(0.5)

        if regional_errors:
            raise RuntimeError("; ".join(regional_errors))

        regional_universes = [regional_by_name[name] for name in XTB_EUROPE_MARKETS]
        universe = combine_market_universes(
            us_nasdaq,
            yahoo_us,
            china_adrs,
            regional_universes,
            us_limit=args.limit,
            market_limit=args.market_limit,
        )
        if not universe:
            raise RuntimeError("No ranking universe could be fetched from Yahoo or Nasdaq.")
        validate_market_coverage(universe, [US_MARKET, *required_markets])
        seed = previous or {"companies": [], "errors": []}
        seed["universe"] = universe
        seed["metadata"] = {}
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
