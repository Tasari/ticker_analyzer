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
- Separate range selectors for Growth, Fundamentals, and Value.
- Rule-based scorecards with configurable metric weights, thresholds, tab weights, and rating labels.
- Editable scoring configuration saved locally in `metrics_config.json`.
- Missing data warnings and partial ratings when enough, but not all, tabs can be scored.
- Basic charts for adjusted price history, financial trends, assets, and debt.

## Scoring Notes

- Growth range metrics use CAGR where positive starting and ending values are available.
- Price momentum uses adjusted prices and skips the most recent month when enough history exists.
- Analyst revenue and EPS estimate growth prefer structured `yfinance` estimate tables, with fallback to `info` fields.
- Fundamentals are built for a default industrial profile. Bank, insurance, asset management, capital markets, mortgage, and REIT-like companies can have non-applicable industrial metrics.
- Value metrics compare current valuation multiples against approximate historical medians for the selected Value range.
- Analyst price target has a low default weight, and the benchmark upside metric is visible but has zero default weight to avoid double counting.

## Configuration

`metrics_config.json` controls:

- tab weights,
- overall rating thresholds and labels,
- tab rating thresholds and labels,
- missing-data policy,
- metric weights, thresholds, directions, units, descriptions, and optional benchmarks.

The app validates the config when loading or saving it. Invalid JSON or inconsistent settings are rejected with an error.
