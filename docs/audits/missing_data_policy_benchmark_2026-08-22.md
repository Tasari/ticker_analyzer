# Missing-data policy benchmark — 2026-08-22

This report compares three scoring policies without changing the production v5.1 configuration:

- `baseline`: current renormalization of available weights.
- `coverage_penalty`: subtract up to 20 points in proportion to missing designed weight.
- `coverage_caps`: full rating range at 85%+ coverage, maximum `strong` at 70–85%, and maximum `neutral` below 70%.

All policies used identical deterministic metric-dropout samples. The run covered 28 companies (22 Industrial and 6 Financial), 500 trials, and dropout rates of 5%, 10%, 15%, 20%, and 30%. The complete machine-readable result is in [missing_data_policy_benchmark_2026-08-22.json](missing_data_policy_benchmark_2026-08-22.json).

## Selected results

| Dropout | Segment | Policy | Spearman | Rating flips | Proper upgrades | Upgrade share among proper changes | Insufficient data |
|---:|---|---|---:|---:|---:|---:|---:|
| 10% | All | baseline | 0.9441 | 18.73% | 9.87% | 72.09% | 5.04% |
| 10% | All | coverage penalty | 0.9423 | 20.24% | 4.18% | 27.49% | 5.04% |
| 10% | All | coverage caps | 0.9441 | 18.19% | 9.33% | 70.94% | 5.04% |
| 10% | Financial | baseline | 0.9002 | 13.47% | 4.07% | 75.31% | 8.07% |
| 10% | Financial | coverage penalty | 0.8854 | 23.13% | 1.30% | 8.63% | 8.07% |
| 10% | Financial | coverage caps | 0.9002 | 13.47% | 4.07% | 75.31% | 8.07% |
| 20% | All | baseline | 0.8494 | 39.78% | 13.11% | 60.24% | 18.01% |
| 20% | All | coverage penalty | 0.8570 | 44.83% | 6.59% | 24.55% | 18.01% |
| 20% | All | coverage caps | 0.8494 | 36.81% | 9.67% | 51.42% | 18.01% |
| 20% | Financial | baseline | 0.7099 | 46.57% | 7.90% | 46.29% | 29.50% |
| 20% | Financial | coverage penalty | 0.7103 | 51.50% | 3.67% | 16.67% | 29.50% |
| 20% | Financial | coverage caps | 0.7099 | 48.13% | 7.80% | 41.86% | 29.50% |

## Initial decision

Neither experimental policy should replace production scoring yet.

The bounded coverage penalty substantially reduces upgrades caused by missing data, but it increases total rating flips, downgrades, and absolute score movement, especially for Financial companies. The tested coverage caps preserve score and ranking behavior but are too weak at 10% dropout and do not address the Financial single-metric capital dependency.

The next experiment should sweep penalty strengths and coverage bands, then repeat the comparison on a materially larger Financial sample. Removing the `equity_to_assets` dependency remains blocked on a subtype-specific capital-metric availability study.
