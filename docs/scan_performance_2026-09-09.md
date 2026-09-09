# Case Capital Full-Terminal Scan Performance - 2026-09-09

The complete observation-level report is [scan_performance_2026-09-09.csv](C:\Users\tcase\OneDrive\Documents\Case Cap\CaseCapitalTradingTerminal-audit\docs\scan_performance_2026-09-09.csv).

Source: persisted VPS `scan_results`; six scans were found for the ET trading date. Repeated ticker appearances across scan times and layers are retained. Return is `(today close - scan price) / scan price`. This is mark-to-close research, not realized P&L.

## Coverage

| Layer | Observations |
|---|---:|
| Core | 146 |
| Strategy candidates | 609 |
| Lottery raw candidates | 1,320 |
| Pharma shock | 40 |
| **Total** | **2,115** |

Unique validated tickers: **397**. The source contained **846 malformed symbol observations** with a duplicated Finviz logo prefix; those were normalized and retained in `ticker_raw` and `ticker_normalized`.

## Return Bands

| Band | Observations | Unique tickers | PM-approved unique tickers |
|---|---:|---:|---:|
| Below -20% | 52 | 22 | 0 |
| -20% to -10% | 105 | 47 | 2: LDOS, PLAY |
| -10% to 0% | 1,215 | 291 | 4: CRML, SHOE, CAPR, INBX |
| 0% to <10% | 645 | 187 | 3: LOVE, ALMU, GOLD |
| 10% to <20% | 51 | 20 | 0 |
| 20% to <30% | 26 | 11 | 0 |
| 30% and higher | 21 | 8 | 0 |
| Close unavailable | 0 | 0 | 0 |

The unique-ticker CSV is sorted by return and contains one first validated observation per ticker. The PM overlay adds action, score, allocation, and approval scan timestamp. PM approval means `STARTER` or `ACCUMULATE`; it does not mean an order was submitted or filled.

## Data Qualification

Close prices were requested from the VPS terminal price waterfall for September 9. This is not realized P&L: it excludes commissions, spread, slippage, order eligibility, position sizing, halts, and execution timing. Normalization corrected the parser’s one-character Finviz logo prefix; it did not alter persisted scan records.
