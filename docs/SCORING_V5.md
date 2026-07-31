# Scoring v5

Scoring v5.1 is an audit-focused screening model. Missing observations remain missing. Overall requires Fundamentals and at least one additional scored tab; one missing tab carries a visible five-point penalty.

## Data and point-in-time rules

- Growth charts use adjusted prices; valuation multiples use raw close prices with period-appropriate share counts.
- SEC company facts include amended annual and quarterly forms. Cumulative YTD facts are converted to discrete quarters, and each observation retains filing/accession provenance.
- Provider output is merged per fact and period. Earlier providers win only for the populated observation; later providers fill individual gaps.
- Historical multiples sample monthly prices and only use facts available by each observation date.

## Ratings and Data Quality

Ratings use stable semantic codes; display labels cannot alter gating. Data Quality below 40 produces `insufficient_data`. DQ 40–54 allows a rating capped at Hold with Low confidence; DQ 55–64 allows up to Buy with Medium confidence; DQ 65+ has High confidence and full eligibility.

Data Quality is the renormalized weighted average of effective positive-weight metric coverage (50%), freshness (25%), source quality (15%), and reconciliation (10%). Reconciliation is `null` and removed from the denominator when only one source is present. Secondary-only, primary-only, and mixed source sets cap DQ at 75, 85, and 92 respectively. Profile fit never changes DQ.

Model applicability is separate. Generic financial fallback has applicability at most 65, blocks Strong Buy, and caps the rating at Buy. Every cap and gate is exposed through `rating_caps`, `warnings`, and `rating_reason_codes`.

The coverage floors are 55% Growth, 60% Fundamentals, and 50% Value. Full-confidence targets are 80%, 80%, and 75%. Positive-weight metrics must belong to exactly one positive-weight group; zero-weight metrics/groups do not affect coverage or DQ. Peer-percentile scoring uses documented p10/p25/p50/p75/p90/p97 anchors when a versioned peer artifact is supplied, while absolute distress guardrails remain active.

Strong Buy starts at Overall 80 and requires DQ 65, Fundamentals 50, and weakest available tab 40. Buy starts at 67 and requires DQ 55, Fundamentals 45, and weakest tab 30. Overall uses the available-tab weighted mean, a five-point missing-tab penalty, and at most a four-point weakest-tab penalty.

## Reproducible rankings

Every ranking checkpoint and row records the full configuration SHA-256 digest, scoring/config/calibration versions, provider and metric schema versions, peer-artifact version, and `data_as_of`. Resume is allowed only when the complete fingerprint matches.

`scripts/calibrate_scoring.py` produces distribution acceptance checks and accepts `--before` to emit ticker-level before/after deltas with scores, DQ, applicability, coverage, caps, warnings, confidence, and reason codes.

## Operations

Production mode is read-only unless an administrator explicitly enables config writes or ranking refresh. Config migrations use timestamped backups and atomic replacement. CI runs lint, tests, coverage, dependency audit, wheel build, and an import smoke test covering the packaged UI.
