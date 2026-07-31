# Scoring v4

Scoring v4 is an auditable screening model. Missing observations remain missing; they are never converted to neutral scores. Every result exposes `scoring_version`, `config_version`, `calibration_version`, profile, metric coverage, Data Quality, diagnostics, and metric-level provenance.

## Rating and tab composition

Metric anchors keep the piecewise 25/75 model: `warn` is 25 points, the midpoint is 50, and `good` is 75. Overall is 80% of the available weighted tab mean plus 20% of the weakest complete tab. The default policy requires all three tabs.

The base five-point rating is classified before quality gates. Gates are monotonic caps: they may downgrade Strong Buy or Buy, but never upgrade Sell or Strong Sell. Tab completeness is declarative under `profile_rules`; the engine does not contain metric-ID-specific composition conditions.

## Data Quality

Data Quality is a point score, not a probability or percentage. Its configured components are:

- metric weight coverage: 30%;
- complete tabs: 15%;
- filing freshness: 20%;
- actual observation depth: 15%;
- source provenance: 10%;
- analyst-estimate quality: 5%;
- profile fit: 5%.

Provider failures, secondary sources, estimated values, and period mismatches apply explicit penalties. The global maximum is 95. Additional caps apply to incomplete tabs, yfinance-only input, Generic Financial analysis, and specialized financial profiles without regulatory data. The API retains `confidence` as a deprecated compatibility alias with the same numeric value.

## Provenance and point-in-time policy

`DataProvenance` records provider, URL, period end, filing timestamp, fetch timestamp, form, accession number, observation count, fallback level, and whether the source is primary. `CompositeProvider` merges sources in priority order. The included official clients cover SEC company facts/submissions, NBP exchange rates, FDIC institutions/financials, GLEIF LEI records, and FINRA broker-dealer lookup. `SecCompanyFactsProvider` converts primary US-GAAP facts into normalized statements.

Historical valuation uses facts only after they became available. SEC frames carry explicit filing dates. A conservative 90-day delay is used when a secondary source exposes only fiscal period ends. This avoids same-period year-end look-ahead.

## Profiles

Classification order is manual override, regulatory identifier, SIC/form evidence, then provider metadata. The configured profiles are Industrial, FinancialBank, FinancialBroker, FinancialLender, FinancialInsurance, FinancialAssetManager, REIT, and fallback Financial. Specialized profiles use the validated financial fallback metric set when dedicated regulatory fields are absent; their Data Quality is capped until regulatory provenance is present.

## Calibration and peers

The bootstrap calibration identifier is `v4-bootstrap-2026Q3`. `scripts/calibrate_scoring.py` reports overall and per-profile distributions, cross-tab correlations, metric missingness, and exceptional cases. Peer-relative metrics consume versioned values under `peer_medians`; without a matching artifact they remain missing. Thresholds must not be dynamically inferred from the company being scored.

## Config migration

`metrics_config.json` is version 4 and declares its scoring model. Loading v3 performs a deterministic in-memory migration and validation. To create an explicit artifact:

```powershell
python scripts/migrate_config.py old_v3.json --output scoring_v4.json
```

An in-place v3 migration writes a `.v3.bak` backup. v2 is rejected because it predates the current anchor semantics and requires manual review. Validation checks tab-weight sums, group-weight sums, unique group membership, metric references, profile rules, thresholds, and the scoring-model identifier.
