# Changelog

## Valuation diagnostics and ranking compatibility — 2026-09-09

- Add a Valuation evidence panel with actual versus Yahoo multiples, reporting periods, fallback labels and historical coverage.
- Flag large provider discrepancies and thin valuation history without changing scoring thresholds.
- Stop treating missing debt/cash as zero in EV/EBITDA; preserve valid zero balances and explicitly label provider fallback.
- Exclude non-positive EBITDA from historical comparison and cache repeated historical ratios within one analysis.
- Warn about old, unknown or mixed stock-ranking calculation/configuration versions, preserving imported snapshots unchanged.
- Add regression and Streamlit UI tests for the new diagnostics and compatibility checks.

## ADR recognition and consistent valuation periods — 2026-09-09

- Add verified BABA/HTHT ADS ratios and recognize SPOT/ASML ordinary US listings without guessing from domicile.
- Share current statement-based multiples between absolute Value, historical comparison, PEG and Fair Value earnings inputs.
- Select complete consecutive-quarter TTM or an explicitly dated annual fallback; keep balance-sheet data as snapshots.
- Respect filing availability in historical TTM, reject non-positive earnings instead of substituting a positive provider P/E, and identify provider discrepancies in notes.
- Fetch quarterly statements through the public Yahoo fallback and invalidate old analysis/metric caches.
- Fix an import-order cycle between analysis and metrics; add period, ADR and fallback-provider regression tests.

## Cross-market valuation correctness — 2026-09-08

- Convert current issuer capitalization and historical quote prices into reporting currency for statement-based valuation.
- Apply verified TSM/FUTU receipt-to-ordinary-share ratios to historical prices and capitalization reconstruction.
- Keep Fair Value earnings inputs in quote currency per listed unit and prevent incompatible statement currency merges.
- Require USD capitalization for ranking filters and tie-breaks; explicitly request USD TradingView financial fields and correct ETF turnover units.
- Preserve original ranking monetary amounts and expose valuation FX/share metadata; missing conversion evidence leaves affected metrics unavailable.
- Share a bounded FX cache across companies, with historical cross-rate fallback through USD.

## v5.2 Value calibration — 2026-08-26

- Added absolute P/S, P/E, EV/EBITDA, and financial P/B anchors alongside historical comparisons.
- Tightened relative-value, free-cash-flow-yield, growth-adjusted, and analyst-target thresholds so plausible extremes no longer saturate at 100.
- Required both absolute and historical valuation evidence for a complete Value score.
- Added current-versus-historical-median details and a weighted Value component breakdown.
- Added per-company strongest signals, weakest signals, rating constraints, and improvement actions.

## v5.1 calibration hotfix — 2026-07-31

- Decoupled Data Quality from model applicability and rating confidence.
- Allowed partial overall scores when Fundamentals and one other tab are available.
- Added DQ confidence bands, rating caps, warnings, and reason codes.
- Made reconciliation optional and renormalized the remaining DQ components.
- Lowered coverage floors and replaced the weakest-tab blend with a maximum four-point penalty.
- Calibrated Strong Buy/Buy thresholds and gates; generic financial fallback now caps at Buy.
- Added percentile-score infrastructure, absolute distress guardrails, regression acceptance checks, and before/after reports.
- Fixed the ranking UI's empty default view by showing all Data Quality levels initially.
- Added a rolling-deployment compatibility layer for Streamlit config validation.
