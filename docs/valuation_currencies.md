# Cross-market valuation units

Quote prices, statement totals and share counts have separate unit contracts.
The Analyzer shows prices in the major quote currency (GBp/GBX becomes GBP).
Income, balance-sheet and cash-flow data retain their reporting currency. Current
statement-based multiples convert issuer market capitalization into that currency
before combining it with profit, equity, debt or cash flow. The issuer's reported
total capitalization is never divided by an ADR ratio.

Historical valuation uses each observation's FX rate and price per **ordinary
share**: quote price × quote-to-reporting FX ÷ ordinary shares per receipt.
That price can be multiplied by the statement's ordinary share count. Growth
charts and simulation price histories are unaffected by this valuation-only view.
Fair Value earnings per listed unit are reconstructed from quote price/P-E, so
raw reporting-currency EPS cannot silently replace quote-currency earnings.

FX rates come from Yahoo daily chart data. Missing direct crosses can be completed
through USD. Only observations on or before the requested day and at most seven
days old are accepted; a future rate is never backfilled. A bounded, hourly cache
is shared across companies and caches failures to limit repeated requests. Missing
or conflicting statement currency, missing FX, and an unverified receipt/share
conversion make the affected derived metrics unavailable. Provider-supplied
dimensionless multiples can still be displayed. Statements with incompatible
currency metadata are not merged cell by cell.

## Depositary receipts

The verified program registry covers:

- TSM: five ordinary shares per ADR, documented in the
  [2003 prospectus](https://www.sec.gov/Archives/edgar/data/1046179/000095016803002302/d424b1.htm)
  and [2026 quarterly financial statements](https://investor.tsmc.com/chinese/encrypt/files/encrypt_file/qr/phase4_reports/2026-04/5b6b7f218129782a5bf0366e7fd06aadc5c1515a/FS.pdf).
- FUTU: eight ordinary shares per ADS since its
  [2019 IPO](https://ir.futuholdings.com/news-releases/news-release-details/futu-announces-pricing-initial-public-offering/),
  also confirmed by the [issuer FAQ](https://futuholdings.gcs-web.com/resources/investor-faqs/).
- BABA: eight ordinary shares per ADS from 2019-07-30, after the
  [share split and ADS ratio change](https://www.sec.gov/Archives/edgar/data/1577552/000110465919042446/a19-16252_1ex99d1.htm).
- HTHT: ten ordinary shares per ADS following the June 2021 subdivision,
  documented in the [issuer annual report](https://ir.hworld.com/static-files/8be68f35-dafb-487c-aa57-7646be1868a6).
  Historical normalization conservatively starts on 2021-07-01, after the change.

Verified ordinary US listings are not classified as unknown ADRs just because
their issuer is foreign: [SPOT](https://www.sec.gov/Archives/edgar/data/1639920/000162828026006874/ck0001639920-20251231.htm)
and [ASML](https://www.sec.gov/Archives/edgar/data/937966/000093796624000008/exhibit21.htm)
use one ordinary share per listed unit. An integration can also supply
`instrumentType` (`ordinary_share` or `common_stock`) with `instrumentTypeSource`.

Provider integrations can supply `ordinarySharesPerReceipt` and
`shareRatioEffectiveFrom`. A ratio without a validity start can support current
capitalization reconstruction but cannot be applied to historical observations.
An unverified foreign US listing is treated as an unknown share basis; a foreign
domicile alone does not prove that a security is an ADR. No ratio is estimated from
current market capitalization or provider share counts. The registry needs review
when depositary programs change; it is not a complete corporate-action database.

## Valuation periods

Absolute Value multiples and their current-versus-history comparisons use the
same statement-based current multiple. P/E also supplies the earnings input for
Fair Value and the growth-adjusted valuation metric. Yahoo's reported multiple is
shown separately in the metric note; it is only a fallback when reconstruction
is unavailable, not a replacement for a non-positive statement denominator.

Revenue, common-shareholder net income, EBITDA, operating cash flow and free cash
flow use four consecutive, non-missing quarters when available. Gaps must be
between 60 and 120 days; incomplete or semiannual series cannot silently become
TTM. The latest valid reporting period wins, with TTM preferred over annual data
for the same period. Otherwise the metric explicitly says `Annual fallback` and
gives the period end. Balance-sheet amounts are snapshots, never quarterly sums.
Free cash flow can be derived per quarter from operating cash flow minus absolute
capital expenditure, preserving any explicit reported FCF values.

Historical comparisons sample monthly prices and use the latest statement
observation available at each sample. TTM only becomes usable once all four
component quarters are available. Explicit filing dates are respected; absent
those, a conservative 90-day publication lag is applied to each quarter/annual
report. This is still secondary, potentially restated Yahoo data, not a fully
point-in-time filings archive. Older periods may fall back to annual data when
Yahoo's quarterly history is short. Notes explicitly identify this mixed history.

The public Yahoo fallback retrieves annual and quarterly statements in the same
request, retaining reporting-currency metadata for both. Cached analysis and
ranking metric versions change so newly calculated results use the new policy;
existing ranking snapshots still require an update.

Live smoke testing on 2026-09-09 produced a Value rating for BABA, SPOT, PKN.WA,
MSFT, SHEL.L, 9988.HK, ASML and HTHT. For all eight, current P/E matched the value
used in the historical-comparison note. The public fallback also returned usable
TTM quarters in PLN for PKN.WA and CNY for BABA. This checks integration and unit
consistency, not independent verification of every Yahoo financial statement.

## Valuation evidence and incomplete enterprise value

Company Details includes a `Valuation evidence` panel for P/S, P/E, P/B and
EV/EBITDA. It shows the actual multiple used, statement period or unverified
provider fallback, Yahoo's supplied multiple, signed percentage difference, and
usable monthly observations out of the selected historical window. A difference
of at least 25% or less than 50% historical coverage triggers a review note. These
are transparent diagnostic thresholds, not new scoring gates and not proof that
either data source is correct. Missing Yahoo values are not compared against zero.

Statement-derived enterprise value requires both debt and cash. A reported zero
is valid; missing debt/cash is not silently replaced with zero. If reconstruction
is impossible, an available positive Yahoo EV/EBITDA is explicitly identified as
a provider fallback. Historical samples with missing debt/cash or non-positive
EBITDA are excluded. Repeated historical comparisons within a single analysis
reuse a compact per-context cache without retaining data across companies.

## Rankings

Stock capitalization and ETF traded value are explicitly compared in USD. Original
received values, their currency and applied conversion factor are retained.
Yahoo screener capitalization is converted from its declared currency; Nasdaq
capitalization is USD. TradingView requests explicitly select financial conversion
to USD with `price_conversion`; prices remain in the symbol's currency. This follows
the distinction in [TradingView's currency conversion documentation](https://www.tradingview.com/support/solutions/43000639101-currency-conversion-in-the-stock-screener/)
and was checked against live GPW and LSE responses. In particular, USD traded value
must not be divided by 100 merely because the associated quote is in GBX.

When the public Yahoo provider consumes a USD ranking universe, it converts its
capitalization back to the quote currency before valuation. Unknown-currency legacy
snapshots remain readable but their capitalization is excluded from USD threshold
filters and cross-market capitalization tie-breaks. Regenerate snapshots to populate
the new currency fields and recalculate scores. Old ETF scores are not retroactively
repaired by relabelling an imported file.

Saved stock rankings are checked against the current calculation/configuration
fingerprint when displayed, including row-level mismatches in mixed snapshots.
Old or missing version metadata triggers a visible warning and a comparison in
`Ranking calculation versions`; imports remain viewable and are never silently
rescored or relabelled. Matching versions are not a freshness guarantee: fetch
dates, selected ranges and available provider data can still produce different
results. Use `Update all rankings` to replace a snapshot with new calculations.

Data limitations remain visible in the Analyzer's valuation-basis section and
missing-data details. For example, live BGEO.L testing on 2026-09-08 recovered a
current GBP/GEL conversion, but Yahoo exposed too little historical GEL FX for a
complete three-year valuation history; those observations are deliberately excluded.
