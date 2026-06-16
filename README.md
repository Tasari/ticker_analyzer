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

- Search and select up to five tickers supported by `yfinance`.
- Compare selected companies in a Summary view, then inspect each company in detail.
- Separate Growth, Fundamentals, and Value tabs.
- Separate range selectors for Growth, Fundamentals, and Value. Fundamentals metrics use medians over the selected annual range where applicable.
- Available analysis ranges are `1Y`, `2Y`, and `3Y`. A 3Y maximum is more reliable across Growth, Fundamentals, and Value because longer CAGR windows often exceed the statement depth available from yfinance.
- Rule-based scorecards with configurable metric weights, thresholds, tab weights, and rating labels.
- Editable scoring configuration saved locally in `metrics_config.json`.
- Missing data warnings, provider-failure diagnostics, and partial ratings when enough, but not all, tabs can be scored.
- Weighted data coverage and High/Medium/Low confidence indicators for the overall result and every analysis tab.
- Automatic Industrial and Financial analysis profiles.
- Basic charts for adjusted price history, financial trends, assets, and debt.

## Scoring Notes

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
- Industrial Growth includes gross-margin trend and share-count CAGR to surface improving unit economics, dilution, and buybacks.
- Industrial Fundamentals include ROIC, FCF margin, accruals ratio, and net debt to EBITDA as quality and balance-sheet signals.
- The Ohlson distress estimate is informational and has zero default scoring weight until it can be properly calibrated.
- Analyst price target has a low default weight, and the benchmark upside metric is visible but has zero default weight to avoid double counting.
- Coverage is calculated from scored metric weights, so missing zero-weight informational metrics do not reduce confidence.

## Configuration

`metrics_config.json` controls:

- tab weights,
- overall rating thresholds and labels,
- tab rating thresholds and labels,
- missing-data policy,
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

Metric IDs referring to a historical median use `selected_median`, because the comparison period follows the Value range selected in the UI. Config version 2 automatically migrates the older fixed-range IDs when loading them.
