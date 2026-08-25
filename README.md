# Stock Analyzer

> [!WARNING]
> **Important disclaimer:** This is a private, experimental research tool—not investment advice or a personalized recommendation. Automated scores and labels such as “Buy” or “Strong Buy” may be incomplete, delayed, inaccurate, or wrong. Investing involves risk, including the possible loss of all capital. Independently verify all information and remain solely responsible for every decision. Read the full [DISCLAIMER.md](DISCLAIMER.md) before using, copying, or distributing the application.

Local Streamlit app for rule-based stock analysis with primary-source provenance and a `yfinance` fallback.

The app is a screening tool, not financial advice. Scores depend on available Yahoo Finance data and the editable local configuration in `metrics_config.json`. Before using, copying, or distributing it, read the full [disclaimer](DISCLAIMER.md).

Copyright (c) 2026 Patryk. All rights reserved. This repository is publicly visible
for portfolio and development-history purposes; no permission is granted to use,
copy, modify, redistribute, or deploy the software. See [COPYRIGHT.md](COPYRIGHT.md).

## Setup

```powershell
pip install -r requirements.txt
streamlit run app.py
```

The entire Streamlit UI is protected by a local password gate. No environment variables or Streamlit Secrets are required: `site_access.json` contains only a salted PBKDF2-SHA256 hash, never the plaintext password. To replace the password later, run:

```powershell
python scripts/set_site_password.py
```

Use `--generate` to create a strong random password automatically. Authentication lasts for the current Streamlit session, and the sidebar `Lock app` button ends it immediately. This is a lightweight single-password gate for a private app, not a multi-user identity system.

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

- Password protection is evaluated before browser preferences or application views are loaded. The repository stores only a salted password hash, and changing it requires no deployment secrets.
- Search and select any number of tickers supported by `yfinance`, including international listings. Exact Yahoo symbols remain addable when autocomplete is unavailable, and a market selector builds common suffixes such as `.WA`, `.L`, `.DE`, and `.HK` from local symbols. Data fetching remains capped at five concurrent workers to avoid provider overload. Successful per-ticker analyses are cached for 15 minutes with a bounded 32-entry cache, so adding companies does not refetch an unchanged comparison without allowing long-lived Streamlit sessions to retain hundreds of full analyses.
- Selected tickers, the active ticker, analysis ranges, and the last open page are stored in that browser for 30 days. Returning sessions restore those preferences and fetch fresh analysis data; full results are never written to browser storage. Restoration runs in the background and never blocks the analyzer if browser storage is unavailable. The sidebar also provides a one-click action to explicitly overwrite the remembered setup with the current one.
- Compare selected companies in a Summary view, then inspect each company in detail. Multi-ticker analysis runs ticker fetches concurrently.
- Simulate a buy-and-hold portfolio from every analyzed ticker with 10,000 initial capital by default, equal or custom weights, fractional shares, independent start/end dates, adjusted prices, and USD/EUR/PLN base-currency conversion. The Simulation tab reports portfolio and position values, P/L, ROI, CAGR, maximum drawdown, annualized volatility, and each ticker's contribution to the result; individual ticker lines can be hidden from the chart.
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
- Ranking snapshots can be exported as validated JSON backups and restored through the UI. Each completed refresh stores a quality report with market coverage, failure categories, rating distribution, and score/rank/rating changes from the previous snapshot.
- A separate Account Statement area imports eToro XLSX statements in memory without persisting the uploaded file. Its Analysis tab reports the period-end portfolio snapshot, cash-flow-adjusted P/L, ROI, annualized ROI, an explicitly estimated Modified Dietz TWR, a reconciled P/L waterfall, and holdings exposure. Separate inclusive start- and end-date controls provide exact realized activity and estimate total P/L, ROI, annualized ROI, and Modified Dietz return for subperiods by combining Account Activity with interpolated unrealized P/L from periodic Holdings snapshots. Instruments can be excluded by their Position-ID-linked ticker, removing their realized transactions, linked fees/dividends, holdings exposure, and closed-position contribution; eToro's aggregate historical unrealized valuations remain explicitly marked as estimated in filtered mode. An optional eToro returns-table CSV replaces estimated TWR/CAGR with compounded monthly returns when it covers the selected range; partial boundary months are geometrically prorated. A normalized growth chart shows the value of an initial 10,000 using the returns table or the statement estimate as fallback, compares the Account Statement portfolio with up to ten cached Yahoo tickers at once, and reports total return, final value, maximum drawdown, relative return, and monthly portfolio returns. Closed Positions provides an exact per-asset realized contribution table for the selected period. The UI reports valuation-anchor distance and coverage warnings so estimates are not presented as exact historical valuations. Its Data preview tab retains the bounded worksheet viewer.

## Large Cap Ranking

The ranking universe contains 1,000 US companies, up to 100 China/Hong Kong ADRs, and up to 100 companies from each configured European market available through XTB: Poland, the United Kingdom, Germany, France, Spain, Italy, Portugal, the Netherlands, Belgium, Austria, Switzerland, Denmark, Finland, Norway, and Sweden. Market quotas are selected independently by local market capitalization, avoiding invalid comparisons between unconverted currencies. Nasdaq classifies some Chinese issuers, including FUTU, under Hong Kong, so both country labels belong to the ADR bucket. European market discovery uses the TradingView stock screener because shared Streamlit Cloud IPs are frequently rate-limited by Yahoo; the resulting symbols are mapped to Yahoo tickers for the existing analysis engine. A snapshot or checkpoint missing any configured market is rejected. Each company is analyzed with the same scoring-v5 engine as the single-stock view. Missing data remains `Insufficient Data` and is not replaced with a neutral score. The UI can filter results by country and exchange.

The locally generated snapshot is stored in the ignored `data/large_cap_ranking_v5.json` file. Generated rankings contain real-company model outputs and are intentionally not committed to the public source repository. Refresh or resume it with:

```powershell
python scripts/build_large_cap_ranking.py --limit 1000 --market-limit 100 --workers 8 --ranges 3Y --public-fallback
```

The job checkpoints every 25 completed companies, and the Streamlit refresh view reports progress only when the checkpoint file changes. A checkpoint is resumed only when its universe contains every configured market and its scoring, config, and calibration versions match. Ranking work is scheduled with a bounded in-flight queue, and refreshes started from the UI use three workers to stay within small hosted-container memory limits. The public fallback uses Yahoo annual fundamentals and price history when the normal crumb-based yfinance client is rate limited.

Use the Ranking backup expander to download the current ignored snapshot before a hosted-container rebuild and import it afterward. Imports are bounded, schema-validated, duplicate-checked, and atomically replace the active snapshot. In production mode, enable this explicitly with `ALLOW_RANKING_IMPORT=true`.

Before a full refresh, run a non-destructive multi-market smoke build. It scans at most 20 US companies plus five each from China ADR, Poland, the United Kingdom, and Germany, uses three workers, disables long analysis retries, and writes to the ignored `data/large_cap_ranking_smoke.json` file. Runtime and peak traced Python memory are included in its metadata:

```powershell
python scripts/build_large_cap_ranking.py --smoke
```

Add `--profile` to a normal build to store the same measurements in its snapshot metadata.

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

- `ticker_analyzer/analysis/engine.py`: lightweight analysis orchestration and result assembly.
- `ticker_analyzer/analysis/`: aggregation, profile selection, provenance, and quality evaluation.
- `ticker_analyzer/metrics/builder.py`: builds the raw metric dictionary and chart data.
- `ticker_analyzer/metrics/formulas.py`: Growth and Fundamentals business formulas.
- `ticker_analyzer/metrics/valuation.py`: current and point-in-time historical valuation comparisons.
- `ticker_analyzer/metrics/estimates.py`: analyst estimates, targets, and forecast growth.
- `ticker_analyzer/metrics/utils.py`: reusable statement, range, CAGR, and ratio helpers.
- `ticker_analyzer/engine.py`: backward-compatible exports for existing imports.
- `ticker_analyzer/data_provider.py`: yfinance access and normalization.
- `ticker_analyzer/providers.py`: lazy compatibility facade for provider integrations.
- `ticker_analyzer/provider_*.py`: HTTP infrastructure, provider composition, SEC, and reference-data clients.
- `ticker_analyzer/data_quality.py`: Data Quality components, penalties, and caps.
- `ticker_analyzer/scoring.py`: metric and tab scoring; `ratings.py` owns rating decisions and gates.
- `ticker_analyzer/ranking_*.py`: universe discovery, batch analysis, storage, and public fallback data.
- `ticker_analyzer/ui/`: lazy page facade plus separate analysis, ranking, configuration, and sidebar modules.

Metric IDs referring to a historical median use `selected_median`, because the comparison period follows the Value range selected in the UI. Config v5 is active. v3/v4 are migrated explicitly; v2 is rejected because its threshold semantics cannot be migrated safely. See `docs/SCORING_V5.md` for the model, data-source, and migration details.

See `docs/ARCHITECTURE.md` for runtime boundaries, lazy-loading rules, and resource invariants.

## Scoring Robustness Audit

Run the deterministic metric-dropout audit to measure how sensitive scores and ranks are to missing data. It removes the rounded metric count corresponding to 10% and 20% of each company's available metrics from positive-weight groups, recomputes tab coverage, Data Quality, overall score, and rating gates, then reports Spearman rank correlation, rating flips, unavailable scores, and rank movement for the whole sample and Industrial/Financial segments. The optional sample output makes later runs network-free:

```powershell
python scripts/audit_scoring_robustness.py --live --trials 100 --output robustness_report.json --sample-output robustness_sample.json
python scripts/audit_scoring_robustness.py robustness_sample.json --trials 500 --seed 20260822
```

The compact production ranking does not contain metric-level results and is intentionally rejected by this audit; retaining all metrics for thousands of companies would materially increase its storage and memory footprint.

Production deployments are read-only by default. Set `APP_MODE=production`; only administrators should opt in to `ALLOW_CONFIG_WRITE=true` or `ALLOW_RANKING_REFRESH=true`. Local mode keeps both controls available for development.

Transient yfinance, Nasdaq, and public-Yahoo failures are retried with bounded exponential backoff. HTTP clients honor `Retry-After`, reuse connection pools per worker, and bound their ETag cache to avoid unbounded long-session memory growth.
