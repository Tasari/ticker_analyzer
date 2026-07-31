# Stock Analyzer

Local Streamlit app for rule-based stock analysis using `yfinance`.

The app is a screening tool, not financial advice. Scores depend on available Yahoo Finance data and the editable local configuration in `metrics_config.json`.

## Setup

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Tests

```powershell
pip install -e ".[dev]"
ruff check .
coverage run -m unittest discover -s tests -v
coverage report
```

The suite contains formula-level tests and network-free integration tests for the full analysis engine. GitHub Actions runs linting and coverage-backed tests on Python 3.11 and 3.12 for every push to `main` and every pull request.

## Features

- Search and select any number of tickers supported by `yfinance`; data fetching remains capped at five concurrent workers to avoid provider overload.
- Compare selected companies in a Summary view, then inspect each company in detail. Multi-ticker analysis runs ticker fetches concurrently.
- Separate Growth, Fundamentals, and Value tabs.
- Separate range selectors for Growth, Fundamentals, and Value. Fundamentals metrics use medians over the selected annual range where applicable.
- Available analysis ranges are `1Y`, `2Y`, and `3Y`. A 3Y maximum is more reliable across Growth, Fundamentals, and Value because longer CAGR windows often exceed the statement depth available from yfinance.
- Rule-based scorecards with configurable metric weights, thresholds, tab weights, and rating labels.
- Editable scoring configuration saved locally in `metrics_config.json`.
- Missing data warnings, provider-failure diagnostics, explicit coverage, and `Insufficient Data` when any tab is incomplete.
- Separate weighted coverage and quality-based confidence (capped at 95) with a diagnostic breakdown.
- Automatic Industrial and Financial analysis profiles.
- Basic charts for adjusted price history, financial trends, assets, and debt.
- A precomputed Large Cap Ranking covering 1,000 equities ordered by scoring-v3 Overall Score.

## Large Cap Ranking

The ranking universe is the 1,000 largest equities returned by the Nasdaq stock screener, ordered by market capitalization. Each company is analyzed with the same scoring-v3 engine as the single-stock view. Missing data remains `Insufficient Data` and is not replaced with a neutral score.

The snapshot is stored in `data/large_cap_ranking_v3.json`. Refresh or resume it with:

```powershell
python scripts/build_large_cap_ranking.py --limit 1000 --workers 5 --ranges 3Y --public-fallback
```

The job checkpoints every ten completed companies. The public fallback uses Yahoo annual fundamentals and price history when the normal crumb-based yfinance client is rate limited; unavailable analyst consensus data lowers coverage and confidence normally.

## Scoring Notes

- Scoring version 3 maps `warn` to 25 points, `good` to 75, and their midpoint to a neutral 50. Zero and 100 require values a full threshold span beyond the anchors.
- Missing, NaN, and infinite values are unavailable rather than neutral; each tab has minimum weighted coverage and composition gates.
- Tab metrics are grouped to reduce double counting: historical multiples in Value and solvency metrics in Fundamentals are aggregated as related signal families.
- Overall Score is 80% of the configured weighted tab mean plus 20% of the weakest tab and requires all three tabs.
- Strong Buy requires Overall ≥85, Confidence ≥75, and every tab ≥45. Buy also has confidence and Fundamentals gates.

- Revenue and operating cash flow growth prefer TTM-vs-TTM CAGR for the selected range and clearly fall back to annual data when yfinance provides too few quarters.
- Other Growth range metrics use CAGR where positive starting and ending values are available.
- Price momentum uses adjusted prices and skips the most recent month. It remains missing when fewer than 13 monthly observations are available.
- Analyst revenue and EPS estimate growth prefer structured `yfinance` estimate tables, with fallback to other estimate fields.
- EPS estimates crossing zero are treated as a turnaround and excluded from percentage-growth scoring.
- Industrial companies use balance-sheet, liquidity, cash-flow, interest-coverage, and distress metrics.
- Financial companies use a separate profile with financial-sector metrics such as equity to assets, return on assets, return on equity, net margin, P/E vs historical median, and P/B vs historical median.
- Value metrics compare statement-aligned current valuation multiples against approximate historical medians for the selected Value range, with reported yfinance multiples used only as fallbacks when a current multiple cannot be computed from available statements.
- Multi-year Fundamentals and Value medians require at least two valid historical observations and report the actual observation count used.
- Industrial Value includes Free Cash Flow Yield.
- Industrial Growth includes operating- and gross-margin trends plus share-count CAGR; price momentum has only a small weight.
- Industrial Fundamentals include operating margin, ROIC, FCF margin, accruals ratio, and net debt to EBITDA, split into quality and solvency groups.
- The Ohlson distress estimate is informational and has zero default scoring weight until it can be properly calibrated.
- Analyst price target has a low default weight, and the benchmark upside metric is visible but has zero default weight to avoid double counting.
- Coverage is calculated from scored metric weights, so missing zero-weight informational metrics do not reduce confidence.

## Configuration

`metrics_config.json` controls:

- tab weights,
- overall rating thresholds and labels,
- tab rating thresholds and labels,
- missing-data policy,
- minimum tab coverage and metric groups,
- profile-specific metric sets,
- metric weights, thresholds, directions, units, descriptions, and optional benchmarks.

The app validates the config when loading or saving it. Invalid JSON or inconsistent settings are rejected with an error.

## Architecture

- `ticker_analyzer/analysis/engine.py`: analysis orchestration, profile selection, scoring, and result assembly.
- `ticker_analyzer/metrics/builder.py`: builds the raw metric dictionary and chart data.
- `ticker_analyzer/metrics/formulas.py`: Growth and Fundamentals business formulas.
- `ticker_analyzer/metrics/valuation.py`: analyst estimates, targets, and historical valuation comparisons.
- `ticker_analyzer/metrics/utils.py`: reusable statement, range, CAGR, and ratio helpers.
- `ticker_analyzer/engine.py`: backward-compatible exports for existing imports.
- `ticker_analyzer/data_provider.py`: yfinance access and normalization.
- `ticker_analyzer/scoring.py`: metric, tab, and overall scoring.
- `ticker_analyzer/ui/`: Streamlit state, actions, and rendering helpers.

Metric IDs referring to a historical median use `selected_median`, because the comparison period follows the Value range selected in the UI. Config v3 is active; v2 remains readable as a legacy format and older fixed-range IDs are migrated when loading. See `docs/SCORING_V3.md` for the full model and calibration workflow.
