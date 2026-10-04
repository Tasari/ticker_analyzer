# Application architecture

The application is split by runtime responsibility so that a Streamlit rerun loads only the code needed by the active page.

## Runtime flow

```text
app.py
  |-- Large Cap Ranking -> ui/ranking_view.py -> ranking/storage.py
  |                                            -> ui/ranking_actions.py (refresh only)
  |
  |-- ETF               -> ui/etf_view.py -> providers/etf.py
  |                                      -> ui/etf_joint_view.py -> providers/etf_joint.py
  |
  |-- Simulation        -> ui/simulation_view.py -> providers/simulation_data.py
  |                                             -> portfolio/advanced_simulation.py
  |                                             -> ui/simulation_results_view.py
  |
  |-- Account Statement -> ui/account_statement_view.py -> portfolio/statement.py
  |                                                    -> portfolio/statement_workbook.py
  |                                                    -> ui/account_statement_charts.py
  |
  `-- Stock Analyzer    -> ui/stock_view.py -> ui/sidebar.py
                         -> ui/analysis_actions.py
                         -> analysis/engine.py
                              |-- providers/ -> market_data.py / sec.py / clients.py
                              |-- metrics/builder.py -> formulas.py / valuation.py / estimates.py
                              `-- scoring/ -> quality.py / ratings.py
```

Compatibility facades (`ticker_analyzer.engine`, `ticker_analyzer.providers`, `ticker_analyzer.ranking`, `ticker_analyzer.ui.views`, and `ticker_analyzer.ui.actions`) keep existing imports working while resolving their implementations lazily. Ranking and provider implementations live in their respective packages rather than as prefixed files at the package root.

`persistence.py` bridges validated session preferences to browser `localStorage` through an inline Streamlit v2 component. The browser snapshot is user-local, versioned, limited to 50 tickers, and expires after 30 days. Analysis results and provider data remain session-only.

## Module boundaries

- `analysis/` orchestrates a single-company analysis and owns profile selection, aggregation, provenance, and quality evaluation.
- `navigation.py` owns page names independently of browser persistence. `app.py` can load navigation even when a pre-refactor persistence module remains cached during a Streamlit Cloud update. Stock analysis runs only after an explicit Analyze click; restoring preferences and changing selections do not schedule analysis.
- `metrics/` calculates raw business signals. It does not decide final rating gates.
- `scoring/` owns metric and tab scoring, data-quality calculations, robustness audits, labels, caps, and rating rules.
- `portfolio/` owns statement parsing, return-series analysis, performance estimates, and simulations.
- `config/` owns validated configuration persistence and defaults.
- `config/validation.py` contains schema and scoring-policy validation without file access.
- `portfolio/statement_models.py` defines statement result types; `statement_workbook.py` owns bounded XLSX loading and cell parsing. `statement.py` retains portfolio calculations and the original imports.
- `file_io.py` publishes complete JSON files through unique temporary files. Configuration, rankings, and access configuration use the same cleanup and replacement rules. Configuration writes retain `fsync` durability; final replacements are serialized within the process for Windows.
- `runtime_settings.py` keeps environment flags and production mutation rules consistent between widgets and background actions.
- `providers/market_data.py` adapts `yfinance`; the rest of `providers/` contains reusable HTTP, SEC, reference-data, and merge infrastructure.
- `ranking/universe.py`, `ranking/builder.py`, `ranking/provider.py`, and `ranking/storage.py` isolate discovery, scheduling, fallback data, and persistence.
- `ui/` contains presentation and user actions. Analysis and ranking actions are independent so one page does not initialize the other page's dependencies.

## Resource invariants

- Ranking builds keep at most one in-flight analysis per worker and default to three workers from Streamlit.
- Incomplete checkpoints retain the universe needed for resume; completed snapshots do not duplicate it.
- Checkpoints are written every 25 completions, atomically and as a JSON stream.
- Progress polling parses a checkpoint only after its file metadata changes.
- Ranking snapshots are parsed once per unchanged file and reused across Streamlit reruns.
- The ranking read cache retains up to eight file identities so Stocks, ETFs, Crypto, and checkpoints do not evict each other on every rerun.
- Simulation prices, dividends, trailing returns, and correlations are prepared once for both strategies. Their trading state and returned mutable tables remain independent.
- Full stock analyses have a 15-minute, 32-entry cache; ticker searches use a separate 128-entry cache.
- Financial statements fetched from `yfinance` are copied once before normalization.
- The stock provider enables yfinance's public exception reporting consistently across the process. Authentication and network failures reach bounded retries and diagnostics instead of being swallowed into empty results. Attribute retries use a fresh Ticker scraper, because yfinance can mark failed metadata as already fetched. Exhausted requests still retain successful data and use the existing public core-data fallback.
- Public Yahoo fallback sessions are isolated per ranking worker.
- ETF Joint identity uses normalized company names and confirmed name aliases, with ticker identity only when the company name is unavailable. Distinct listings of one issuer contribute separately; duplicate aliases of one listing within a fund contribute once. Original symbols remain visible, and only a supported listing can be sent to Stock Analyzer. Portfolio percentages are summed without share-count conversion or renormalizing partial holdings.
- Validated configuration is cached by file identity while every caller receives an independent mutable copy.

## Compatibility and test rules

- Public facade exports remain available for existing scripts and imports.
- Import-boundary tests ensure lightweight package and ranking-page imports do not initialize pandas, Plotly, yfinance, or the analysis engine unnecessarily.
- Ranking fingerprints include config, scoring, provider, metric, universe, and data-as-of versions; incompatible checkpoints are rebuilt.
- File replacement is atomic for configuration and ranking snapshots.
