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
python -m unittest discover -s tests
```

## Features

- Search and select up to five tickers supported by `yfinance`.
- Compare selected companies in a Summary view, then inspect each company in detail.
- Separate Growth, Fundamentals, and Value tabs.
- Separate range selectors for Growth, Fundamentals, and Value. Fundamentals metrics use medians over the selected annual range where applicable.
- Available analysis ranges are `1Y`, `2Y`, and `3Y`. A 3Y maximum is more reliable across Growth, Fundamentals, and Value because longer CAGR windows often exceed the statement depth available from yfinance.
- Rule-based scorecards with configurable metric weights, thresholds, tab weights, and rating labels.
- Editable scoring configuration saved locally in `metrics_config.json`.
- Missing data warnings and partial ratings when enough, but not all, tabs can be scored.
- Automatic Industrial and Financial analysis profiles.
- Basic charts for adjusted price history, financial trends, assets, and debt.

## Scoring Notes

- Revenue and operating cash flow growth prefer TTM-vs-TTM CAGR for the selected range and clearly fall back to annual data when yfinance provides too few quarters.
- Other Growth range metrics use CAGR where positive starting and ending values are available.
- Price momentum uses adjusted prices and skips the most recent month when enough history exists.
- Analyst revenue and EPS estimate growth prefer structured `yfinance` estimate tables, with fallback to `info` fields.
- Industrial companies use balance-sheet, liquidity, cash-flow, interest-coverage, and distress metrics.
- Financial companies use a separate profile with financial-sector metrics such as equity to assets, return on assets, return on equity, net margin, P/E vs historical median, and P/B vs historical median.
- Value metrics compare current valuation multiples against approximate historical medians for the selected Value range.
- Multi-year Fundamentals and Value medians require at least two valid historical observations and report the actual observation count used.
- Industrial Value includes Free Cash Flow Yield.
- Industrial Growth includes gross-margin trend and share-count CAGR to surface improving unit economics, dilution, and buybacks.
- Industrial Fundamentals include ROIC, FCF margin, accruals ratio, and net debt to EBITDA as quality and balance-sheet signals.
- The Ohlson distress estimate is informational and has zero default scoring weight until it can be properly calibrated.
- Analyst price target has a low default weight, and the benchmark upside metric is visible but has zero default weight to avoid double counting.

## Configuration

`metrics_config.json` controls:

- tab weights,
- overall rating thresholds and labels,
- tab rating thresholds and labels,
- missing-data policy,
- profile-specific metric sets,
- metric weights, thresholds, directions, units, descriptions, and optional benchmarks.

The app validates the config when loading or saving it. Invalid JSON or inconsistent settings are rejected with an error.
