# Changelog

## v5.1 calibration hotfix — 2026-07-31

- Decoupled Data Quality from model applicability and rating confidence.
- Allowed partial overall scores when Fundamentals and one other tab are available.
- Added DQ confidence bands, rating caps, warnings, and reason codes.
- Made reconciliation optional and renormalized the remaining DQ components.
- Lowered coverage floors and replaced the weakest-tab blend with a maximum four-point penalty.
- Calibrated Strong Buy/Buy thresholds and gates; generic financial fallback now caps at Buy.
- Added percentile-score infrastructure, absolute distress guardrails, regression acceptance checks, and before/after reports.
- Fixed the ranking UI's empty default view by showing all Data Quality levels initially.
