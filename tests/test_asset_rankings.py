from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ticker_analyzer.ranking.assets import (
    CRYPTO_RANKING_PATH,
    ETF_RANKING_PATH,
    build_crypto_ranking,
    build_etf_ranking,
    fetch_crypto_market,
    fetch_etfs_for_exchange,
    refresh_crypto_ranking,
    refresh_etf_ranking,
    score_market_rows,
)


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.checked = False

    def raise_for_status(self):
        self.checked = True

    def json(self):
        return self.payload


class AssetRankingTests(unittest.TestCase):
    def test_fetch_etfs_maps_tradingview_fields(self):
        response = Response(
            {
                "data": [
                    {
                        "s": "LSE:CSPX",
                        "d": [
                            "CSPX", "iShares Core S&P 500", 826.68, "USD", "LSE", "Ireland",
                            1.0, 2.0, 3.0, 4.0, 0.75, 68_000_000,
                        ],
                    }
                ]
            }
        )
        request = Mock(return_value=response)

        rows = fetch_etfs_for_exchange(
            50, scanner_market="uk", country="United Kingdom", market="London Stock Exchange",
            yahoo_suffix=".L", request=request,
        )

        self.assertTrue(response.checked)
        self.assertEqual(rows[0]["ticker"], "CSPX.L")
        self.assertEqual(rows[0]["name"], "iShares Core S&P 500")
        self.assertEqual(rows[0]["return_1y"], 4.0)
        sent = request.call_args.kwargs["json"]
        self.assertEqual(sent["filter"][0]["right"], "fund")
        self.assertEqual(sent["sort"]["sortBy"], "Value.Traded")

    def test_fetch_etfs_skips_blank_symbols_and_invalid_numbers(self):
        response = Response({"data": [{"s": "", "d": ["", "Broken", "nope"]}]})
        rows = fetch_etfs_for_exchange(
            1, scanner_market="uk", country="UK", market="LSE", yahoo_suffix=".L",
            request=Mock(return_value=response),
        )
        self.assertEqual(rows, [])

    def test_fetch_crypto_maps_market_fields_and_disambiguates_symbols(self):
        source = {
            "id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "current_price": 100,
            "market_cap": 1000, "market_cap_rank": 1, "total_volume": 100,
            "price_change_percentage_24h": 1, "price_change_percentage_7d_in_currency": 2,
            "price_change_percentage_30d_in_currency": 3,
            "price_change_percentage_200d_in_currency": 4,
            "price_change_percentage_1y_in_currency": 5, "ath_change_percentage": -10,
        }
        response = Response([source, {**source, "id": "wrapped-bitcoin", "name": "Wrapped", "market_cap": 0}])
        request = Mock(return_value=response)

        rows = fetch_crypto_market(100, request=request)

        self.assertEqual(rows[0]["ticker"], "BTC-USD")
        self.assertEqual(rows[0]["volume_market_cap"], 0.1)
        self.assertEqual(rows[1]["ticker"], "BTC-WRAPPED-BITCOIN-USD")
        self.assertIsNone(rows[1]["volume_market_cap"])
        self.assertEqual(request.call_args.kwargs["params"]["per_page"], 100)

    def test_fetch_crypto_skips_missing_symbols_and_caps_limit(self):
        request = Mock(return_value=Response([{"id": "blank", "symbol": ""}]))
        self.assertEqual(fetch_crypto_market(999, request=request), [])
        self.assertEqual(request.call_args.kwargs["params"]["per_page"], 250)

    def test_market_scoring_rewards_returns_and_lower_volatility(self):
        rows = [
            {"ticker": "GOOD", "return": 20, "risk": 1},
            {"ticker": "MID", "return": 10, "risk": 5},
            {"ticker": "BAD", "return": 0, "risk": 10},
        ]
        scored = score_market_rows(rows, {"return": 75, "risk": 25}, lower_is_better=frozenset({"risk"}))
        self.assertEqual([row["ticker"] for row in scored], ["GOOD", "MID", "BAD"])
        self.assertEqual(scored[0]["overall_score"], 100.0)
        self.assertEqual(scored[0]["rating"], "Strong")
        self.assertEqual(scored[-1]["rating"], "Very weak")

    def test_market_scoring_reweights_missing_values_and_requires_half_coverage(self):
        rows = [{"ticker": "A", "return": 10}, {"ticker": "B", "return": None}]
        scored = score_market_rows(rows, {"return": 40, "liquidity": 60})
        by_ticker = {row["ticker"]: row for row in scored}
        self.assertIsNone(by_ticker["A"]["overall_score"])
        self.assertEqual(by_ticker["A"]["data_coverage"], 40.0)
        self.assertEqual(by_ticker["A"]["rating"], "Insufficient data")
        self.assertIsNone(by_ticker["B"]["rank"])

    @patch("ticker_analyzer.ranking.assets.fetch_etfs_for_exchange")
    def test_build_etf_ranking_keeps_partial_exchange_results(self, fetch):
        def result(_limit, **kwargs):
            if kwargs["market"] == "Xetra":
                raise RuntimeError("temporary outage")
            return [{
                "ticker": f"ETF{kwargs['yahoo_suffix']}", "name": kwargs["market"],
                "return_1m": 1, "return_3m": 2, "return_6m": 3, "return_1y": 4,
                "volatility_1m": 1, "traded_value": 100,
            }]

        fetch.side_effect = result
        payload = build_etf_ranking(limit_per_exchange=2)
        self.assertEqual(payload["metadata"]["asset_class"], "ETF")
        self.assertEqual(len(payload["errors"]), 1)
        self.assertGreater(len(payload["companies"]), 0)

    @patch("ticker_analyzer.ranking.assets.fetch_crypto_market")
    def test_build_crypto_ranking(self, fetch):
        fetch.return_value = [
            {
                "ticker": "BTC-USD", "return_7d": 1, "return_30d": 2, "return_200d": 3,
                "return_1y": 4, "volume_market_cap": 0.1, "market_cap": 1000, "ath_drawdown": -5,
            }
        ]
        payload = build_crypto_ranking(1)
        self.assertEqual(payload["metadata"]["asset_class"], "Crypto")
        self.assertEqual(payload["companies"][0]["ticker"], "BTC-USD")

    @patch("ticker_analyzer.ranking.assets.save_ranking")
    @patch("ticker_analyzer.ranking.assets.build_etf_ranking")
    def test_refresh_etf_saves_nonempty_snapshot(self, build, save):
        build.return_value = {"metadata": {}, "companies": [{"ticker": "ETF.L"}], "errors": []}
        self.assertIs(refresh_etf_ranking(), build.return_value)
        save.assert_called_once_with(build.return_value, ETF_RANKING_PATH)

    @patch("ticker_analyzer.ranking.assets.save_ranking")
    @patch("ticker_analyzer.ranking.assets.build_crypto_ranking")
    def test_refresh_crypto_saves_nonempty_snapshot(self, build, save):
        build.return_value = {"metadata": {}, "companies": [{"ticker": "BTC-USD"}], "errors": []}
        self.assertIs(refresh_crypto_ranking(), build.return_value)
        save.assert_called_once_with(build.return_value, CRYPTO_RANKING_PATH)

    @patch("ticker_analyzer.ranking.assets.save_ranking")
    @patch("ticker_analyzer.ranking.assets.build_crypto_ranking")
    def test_empty_refresh_does_not_replace_snapshot(self, build, save):
        build.return_value = {"metadata": {}, "companies": [], "errors": []}
        with self.assertRaisesRegex(RuntimeError, "previous snapshot"):
            refresh_crypto_ranking()
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
