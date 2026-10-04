# Maintenance refactor — 2026-10-04

The review covered application code, providers, metrics, scoring, portfolio calculations, rankings, configuration, UI, and command-line scripts. Generated files, financial datasets, exports, lockfiles, and dependencies were excluded. The review considered reuse, maintainability, and repeated work; changes target verified issues instead of introducing new product flows or changing scoring policy.

## Responsibilities

- `app.py` retains authentication, navigation, browser preferences, and routing. `ui/stock_view.py` owns the analysis lifecycle.
- `config/validation.py` isolates schema validation from loading, migration, and persistence.
- `portfolio/statement_models.py` contains the statement result types. `statement_workbook.py` owns bounded XLSX loading and cell parsing. Statement callers use context-managed workbook cleanup and one shared row/header reader.
- `providers/simulation_data.py` owns concurrent market-history loading, imported-return histories, currency conversion, and their bounded caches. Both adjusted and raw-history paths share scheduling and failure handling.
- `ui/simulation_results_view.py` contains result tables, plots, and risk presentation. `ui/account_statement_charts.py` builds statement figures without widget state.
- `file_io.py` handles atomic JSON publication and cleanup for config, rankings, and access configuration. Writers use independent temporary files and serialize final replacements within the process, preventing Windows replacement races. Existing config durability is retained.
- Shared helpers unify display formats, ticker-suggestion deduplication, year-range parsing, and production mutation flags. Existing public and compatibility imports remain available.

## Reduced repeated work

- Simulation market frames, correlations, and trailing returns are prepared once for both strategies. The trading loop reads rows directly instead of repeatedly looking up individual pandas cells. Strategy balances and mutable result frames remain independent.
- Ranking storage retains eight snapshot identities, covering all three asset tabs and refresh checkpoints. Reading unchanged snapshots repeatedly does not parse their JSON again.
- Statement reconciliation converts scalar values without creating a pandas Series for each cell and formats each period label once.
- Financial statement groups have one definition on `MarketData`; providers and quality calculations reuse it. Diagnostics are formatted once per analysis.

## Verification

- The pre-refactor baseline passed 424 tests. The final suite passes 434 tests with 92% coverage, above the configured 90% gate. Added regressions cover failed and concurrent JSON publication, three-tab ranking cache reuse, simulation input validation, independent strategy outputs, history-download failures, raw prices versus dividends, and order preservation under partial failures.
- A deterministic comparison with the pre-refactor simulation ran 24 scenarios combining contributions, rebalancing frequencies, taxes, costs, cash, reinvested/cash dividends, missing histories, delayed starts, and timezone-aware dates. Every scalar result, position, time series, and correlation matrix matched exactly.
- A local Windows/Python 3.12 benchmark used 20 synthetic price histories over 2020–2024 and both buy-and-hold/monthly-rebalanced strategies. Median of three runs: 3.678 seconds before versus 0.140 seconds after, approximately 26.2× faster. This measures computation only; network and hosted-runtime timings vary.
- Repository lint, wheel packaging, public-import boundaries, and Streamlit interaction tests pass after the refactor. Formatting of changed Python files is separately checked for AST equivalence.

## Corrected edge cases

These corrections are distinct from behavior-preserving code moves:

- Simulations reject NaN and infinite capital, allocations, and assumptions with `SimulationError` instead of producing invalid numerical output.
- The informational Ohlson calculation returns an unavailable metric when both earnings observations are zero and handles the logistic limit for extreme negative scores, avoiding an exception that could abort a stock analysis. Its default scoring weight remains zero.
- Empty simulation history selections return empty collections without constructing an executor with zero workers.

Scoring weights, rating gates, currency conventions, valuation availability dates, ranking formats, existing navigation, and import/export behavior are retained. External provider availability is outside this refactor's control.
