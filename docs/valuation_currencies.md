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

The initial verified program registry covers:

- TSM: five ordinary shares per ADR, documented in the
  [2003 prospectus](https://www.sec.gov/Archives/edgar/data/1046179/000095016803002302/d424b1.htm)
  and [2026 quarterly financial statements](https://investor.tsmc.com/chinese/encrypt/files/encrypt_file/qr/phase4_reports/2026-04/5b6b7f218129782a5bf0366e7fd06aadc5c1515a/FS.pdf).
- FUTU: eight ordinary shares per ADS since its
  [2019 IPO](https://ir.futuholdings.com/news-releases/news-release-details/futu-announces-pricing-initial-public-offering/),
  also confirmed by the [issuer FAQ](https://futuholdings.gcs-web.com/resources/investor-faqs/).

Provider integrations can supply `ordinarySharesPerReceipt` and
`shareRatioEffectiveFrom`. A ratio without a validity start can support current
capitalization reconstruction but cannot be applied to historical observations.
An unverified foreign US listing is treated as an unknown share basis; a foreign
domicile alone does not prove that a security is an ADR. No ratio is estimated from
current market capitalization or provider share counts. The registry needs review
when depositary programs change; it is not a complete corporate-action database.

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

Data limitations remain visible in the Analyzer's valuation-basis section and
missing-data details. For example, live BGEO.L testing on 2026-09-08 recovered a
current GBP/GEL conversion, but Yahoo exposed too little historical GEL FX for a
complete three-year valuation history; those observations are deliberately excluded.
