# Scoring v5

Scoring v5 is an audit-focused screening model. Missing observations remain missing and all three tabs must pass their configured coverage and required-group gates before an overall score is published.

## Data and point-in-time rules

- Growth charts use adjusted prices; valuation multiples use raw close prices with period-appropriate share counts.
- SEC company facts include amended annual and quarterly forms. Cumulative YTD facts are converted to discrete quarters, and each observation retains filing/accession provenance.
- Provider output is merged per fact and period. Earlier providers win only for the populated observation; later providers fill individual gaps.
- Historical multiples sample monthly prices and only use facts available by each observation date.

## Ratings and Data Quality

Ratings use stable semantic codes; display labels cannot alter gating. Data Quality below 60 produces `not_rated`, never a directional recommendation. Generic/specialized financial fallbacks are labeled explicitly, capped at 60/65 Data Quality, and cannot rate above Hold without the relevant regulatory model and evidence.

The default industrial coverage floors are 70% Growth, 75% Fundamentals, and 70% Value. Positive-weight metrics must belong to exactly one configured group. Peer-relative group weight is zero until a versioned peer artifact is configured.

## Reproducible rankings

Every ranking checkpoint and row records the full configuration SHA-256 digest, scoring/config/calibration versions, provider and metric schema versions, peer-artifact version, and `data_as_of`. Resume is allowed only when the complete fingerprint matches.

## Operations

Production mode is read-only unless an administrator explicitly enables config writes or ranking refresh. Config migrations use timestamped backups and atomic replacement. CI runs lint, tests, coverage, dependency audit, wheel build, and an import smoke test covering the packaged UI.
