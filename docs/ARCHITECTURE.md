# Application architecture

The application is split by runtime responsibility so that a Streamlit rerun loads only the code needed by the active page.

## Runtime flow

```text
app.py
  |-- Large Cap Ranking -> ui/ranking_view.py -> ranking_storage.py
  |                                            -> ui/ranking_actions.py (refresh only)
  |
  `-- Stock Analyzer    -> ui/sidebar.py
                         -> ui/analysis_actions.py
                         -> analysis/engine.py
                              |-- providers.py -> provider_*.py / data_provider.py
                              |-- metrics/builder.py -> formulas.py / valuation.py / estimates.py
                              `-- scoring.py -> ratings.py
```

Compatibility facades (`ticker_analyzer.engine`, `ticker_analyzer.providers`, `ticker_analyzer.ranking`, `ticker_analyzer.ui.views`, and `ticker_analyzer.ui.actions`) keep existing imports working while resolving their implementations lazily.

## Module boundaries

- `analysis/` orchestrates a single-company analysis and owns profile selection, aggregation, provenance, and quality evaluation.
- `metrics/` calculates raw business signals. It does not decide final rating gates.
- `scoring.py` scores metrics and tabs; `ratings.py` owns labels, caps, and overall-rating rules.
- `data_provider.py` adapts `yfinance`; `provider_*.py` contains reusable HTTP, SEC, reference-data, and merge infrastructure.
- `ranking_universe.py`, `ranking_builder.py`, `ranking_provider.py`, and `ranking_storage.py` isolate discovery, scheduling, fallback data, and persistence.
- `ui/` contains presentation and user actions. Analysis and ranking actions are independent so one page does not initialize the other page's dependencies.

## Resource invariants

- Ranking builds keep at most one in-flight analysis per worker and default to three workers from Streamlit.
- Incomplete checkpoints retain the universe needed for resume; completed snapshots do not duplicate it.
- Checkpoints are written every 25 completions, atomically and as a JSON stream.
- Progress polling parses a checkpoint only after its file metadata changes.
- Ranking snapshots are parsed once per unchanged file and reused across Streamlit reruns.
- Full stock analyses have a 15-minute, 32-entry cache; ticker searches use a separate 128-entry cache.
- Financial statements fetched from `yfinance` are copied once before normalization.
- Public Yahoo fallback sessions are isolated per ranking worker.
- Validated configuration is cached by file identity while every caller receives an independent mutable copy.

## Compatibility and test rules

- Public facade exports remain available for existing scripts and imports.
- Import-boundary tests ensure lightweight package and ranking-page imports do not initialize pandas, Plotly, yfinance, or the analysis engine unnecessarily.
- Ranking fingerprints include config, scoring, provider, metric, universe, and data-as-of versions; incompatible checkpoints are rebuilt.
- File replacement is atomic for configuration and ranking snapshots.

