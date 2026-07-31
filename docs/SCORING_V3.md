# Scoring model v3

Data flow: raw Yahoo Finance data → metric formulas → finite raw values → metric score → grouped tab aggregation → coverage gates → confidence → weakest-tab-adjusted overall score → rating gates → serialized result → Streamlit UI.

`warn` anchors a metric at 25 points and `good` at 75. Their midpoint is 50; the score reaches 0 or 100 only one additional threshold span beyond those anchors. Missing and non-finite data are never converted to 50.

Growth, Fundamentals, and Value each enforce weighted coverage and composition requirements. Zero-weight informational metrics affect neither score nor coverage. Overall requires all three complete tabs and combines 80% of their configured weighted mean with 20% of the weakest tab.

Confidence describes data quality, not company quality. It combines weighted coverage, statement freshness, selected history length, analyst coverage, and profile fit, is capped at 95, and the generic Financial profile is capped at 80. The API exposes the component breakdown and all applied caps.

Ratings use both score and gates. Strong Buy requires overall ≥85, confidence ≥75, and every tab ≥45. Buy requires overall ≥70, confidence ≥60, Fundamentals ≥40, and every tab ≥30. Missing tabs produce `Insufficient Data`; low confidence or weak Fundamentals/Value caps the rating at Hold.

The generic Financial profile is intentionally conservative: Fundamentals is capped at 85 and confidence at 80. `config_for_profile` can select future `FinancialBank`, `FinancialBroker`, `FinancialLender`, `FinancialInsurance`, and `FinancialAssetManager` entries without changing the scoring engine.

Every serialized result includes `scoring_version`, `config_version`, tab coverage, completeness, group breakdown, confidence, confidence breakdown, and metric-level raw value/score/weight/availability through the existing metric objects. These version fields are cache keys for any future persistent cache; v2 and v3 results must not be mixed.

To calibrate an exported list of API results:

```powershell
python scripts/calibrate_scoring.py results.json --output calibration_report_v3.json
```

The report includes count, mean, median, standard deviation, percentiles, extrema, percentages at 90/95/100, and every Strong Buy, score ≥90, or confidence ≥90 case.
