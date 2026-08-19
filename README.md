# Stock Analyzer

Local Streamlit app for rule-based stock analysis with primary-source provenance and a `yfinance` fallback.

The app is a screening tool, not financial advice. Scores depend on available Yahoo Finance data and the editable local configuration in `metrics_config.json`.

## Setup

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Set `SEC_USER_AGENT` to an application name and contact email (for example `TickerAnalyzer contact@example.com`) to enable SEC company facts as the primary source for US issuers. Without it, the app uses the yfinance fallback and Data Quality records that limitation.

## Tests

```powershell
pip install -e ".[dev]"
ruff check .
coverage run -m unittest discover -s tests -v
coverage report
```

The suite contains formula-level tests and network-free integration tests for the full analysis engine. GitHub Actions runs linting and coverage-backed tests on Python 3.11 and 3.12 for every push to `main` and every pull request.

## Features

- Search and select any number of tickers supported by `yfinance`; data fetching remains capped at five concurrent workers to avoid provider overload. Successful per-ticker analyses are cached for 15 minutes, so adding companies does not refetch an unchanged comparison.
- Compare selected companies in a Summary view, then inspect each company in detail. Multi-ticker analysis runs ticker fetches concurrently.
- Separate Growth, Fundamentals, and Value tabs.
- Separate range selectors for Growth, Fundamentals, and Value. Fundamentals metrics use medians over the selected annual range where applicable.
- Available analysis ranges are `1Y`, `2Y`, and `3Y`. A 3Y maximum is more reliable across Growth, Fundamentals, and Value because longer CAGR windows often exceed the statement depth available from yfinance.
- Rule-based scorecards with configurable metric weights, thresholds, tab weights, and rating labels.
- Editable scoring configuration saved locally in `metrics_config.json`.
- Missing data warnings, provider-failure diagnostics, explicit coverage, and partial ratings when Fundamentals plus one other tab are available.
- Separate metric coverage, Data Quality (0–95 points), model applicability, rating confidence, active caps, and reason codes.
- Industrial, bank, broker, lender, insurance, asset-manager, REIT, and Generic Financial profiles with explicit overrides.
- Basic charts for adjusted price history, financial trends, assets, and debt.
- A precomputed multi-market Large Cap Ranking covering 1,000 US companies plus up to 100 companies per configured international market, ordered by scoring-v5 Overall Score.

## Large Cap Ranking

The ranking universe contains 1,000 US companies, up to 100 Chinese ADRs, and up to 100 companies from each configured European market available through XTB: Poland, the United Kingdom, Germany, France, Spain, Italy, Portugal, the Netherlands, Belgium, Austria, Switzerland, Denmark, Finland, Norway, and Sweden. Market quotas are selected independently by local market capitalization, avoiding invalid comparisons between unconverted currencies. European Yahoo screeners are fetched sequentially with retries because their authentication state is shared; a snapshot or checkpoint missing any configured market is rejected. Each company is analyzed with the same scoring-v5 engine as the single-stock view. Missing data remains `Insufficient Data` and is not replaced with a neutral score. The UI can filter results by country and exchange.

The snapshot is stored in `data/large_cap_ranking_v5.json`. Refresh or resume it with:

```powershell
python scripts/build_large_cap_ranking.py --limit 1000 --market-limit 100 --workers 8 --ranges 3Y --public-fallback
```

The job checkpoints every ten completed companies, and the Streamlit refresh view reports progress from those checkpoints. A checkpoint is resumed only when its universe contains every configured market and its scoring, config, and calibration versions match. Ranking work is scheduled with a bounded in-flight queue, and refreshes started from the UI use three workers to stay within small hosted-container memory limits. The public fallback uses Yahoo annual fundamentals and price history when the normal crumb-based yfinance client is rate limited.

## Scoring Notes

- Scoring version 4 maps `warn` to 25 points, `good` to 75, and their midpoint to a neutral 50. Zero and 100 require values a full threshold span beyond the anchors.
- Missing, NaN, and infinite values are unavailable rather than neutral; each profile declares minimum coverage and required metric groups in config.
- Tab metrics are grouped to reduce double counting: historical multiples in Value and solvency metrics in Fundamentals are aggregated as related signal families.
- Overall Score uses the weighted mean of available tabs, requires Fundamentals plus one other tab, subtracts five points for one missing tab, and applies at most a four-point weakest-tab penalty.
- Data Quality below 40 blocks a rating; 40–54 caps it at Hold, 55–64 caps it at Buy, and 65+ allows the full rating range. Strong Buy starts at 80 and Buy at 67, subject to the documented Fundamentals and weakest-tab gates.

- Revenue and operating cash flow growth prefer TTM-vs-TTM CAGR for the selected range and clearly fall back to annual data when yfinance provides too few quarters.
- Other Growth range metrics use CAGR where positive starting and ending values are available.
- Price momentum uses adjusted prices and skips the most recent month. It remains missing when fewer than 13 monthly observations are available.
- Analyst revenue and EPS estimate growth prefer structured `yfinance` estimate tables, with fallback to other estimate fields.
- EPS estimates crossing zero are treated as a turnaround and excluded from percentage-growth scoring.
- Industrial companies use balance-sheet, liquidity, cash-flow, interest-coverage, and distress metrics.
- Financial companies use a separate profile with financial-sector metrics such as equity to assets, return on assets, return on equity, net margin, P/E vs historical median, and P/B vs historical median.
- Value metrics compare statement-aligned current valuation multiples against point-in-time historical medians. Explicit filing dates are used when available; secondary statements use a conservative 90-day availability lag.
- Multi-year Fundamentals and Value medians require at least two valid historical observations and report the actual observation count used.
- Industrial Value includes Free Cash Flow Yield.
- Industrial Growth includes operating- and gross-margin trends plus share-count CAGR; price momentum has only a small weight.
- Industrial Fundamentals include operating margin, ROIC, FCF margin, accruals ratio, and net debt to EBITDA, split into quality and solvency groups.
- The Ohlson distress estimate is informational and has zero default scoring weight until it can be properly calibrated.
- Analyst price target has a low default weight, and the benchmark upside metric is visible but has zero default weight to avoid double counting.
- Coverage counts only positive-weight metrics/groups. Data Quality combines effective coverage, freshness, source quality, and optional reconciliation; model applicability is reported separately.

## Configuration

`metrics_config.json` controls:

- tab weights,
- overall rating thresholds and labels,
- tab rating thresholds and labels,
- missing-data policy,
- minimum tab coverage and metric groups,
- profile-specific metric sets,
- profile group requirements and ticker overrides,
- Data Quality weights, model-applicability settings, rating caps, guardrails, and versioned peer medians,
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
- `ticker_analyzer/providers.py`: composite provider plus SEC, NBP, FDIC, GLEIF, and FINRA clients.
- `ticker_analyzer/data_quality.py`: Data Quality components, penalties, and caps.
- `ticker_analyzer/scoring.py`: metric, tab, and overall scoring.
- `ticker_analyzer/ui/`: Streamlit state, actions, and rendering helpers.

Metric IDs referring to a historical median use `selected_median`, because the comparison period follows the Value range selected in the UI. Config v5 is active. v3/v4 are migrated explicitly; v2 is rejected because its threshold semantics cannot be migrated safely. See `docs/SCORING_V5.md` for the model, data-source, and migration details.

Production deployments are read-only by default. Set `APP_MODE=production`; only administrators should opt in to `ALLOW_CONFIG_WRITE=true` or `ALLOW_RANKING_REFRESH=true`. Local mode keeps both controls available for development.
