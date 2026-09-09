# 2026-09-09 Unique Scan Performance With PM Overlay

This overlays the day's unique scan-ticker performance with PM approvals. A PM approval is defined as `STARTER` or `ACCUMULATE`; `WATCH` and `REJECT` are not approvals. Repeated appearances across scans are collapsed to the ticker's first validated scan observation.

## Summary

- Unique validated tickers: **397**
- PM approval observations: **43**
- Unique PM-approved tickers: **9**
- Positive approved names: **3**
- Approved names down less than 10%: **4**
- Approved names down 10% to 20%: **2**
- Approved names in the 20%+ bands: **0**

## Overlay Diagram

```text
Below -20%       18 total | PM approved: 0
-20% to -10%     28 total | PM approved: 2  LDOS, PLAY
-10% to 0%      227 total | PM approved: 4  CRML, SHOE, CAPR, INBX
0% to <10%      100 total | PM approved: 3  LOVE, ALMU, GOLD
10% to <20%      13 total | PM approved: 0
20% to <30%       3 total | PM approved: 0
30% and higher   8 total | PM approved: 0
Unavailable       0 total | PM approved: 0
```

## PM-Approved Names

| Ticker | Scan-to-close return | Band | PM action | PM score | Allocation |
|---|---:|---|---|---:|---:|
| LOVE | +9.05% | 0% to <10% | STARTER | 66.5 | $44.20 |
| ALMU | +0.53% | 0% to <10% | STARTER | 65.8 | $44.20 |
| GOLD | +0.37% | 0% to <10% | STARTER | 66.8 | $44.13 |
| CRML | -3.15% | -10% to 0% | STARTER | 65.5 | $44.20 |
| SHOE | -3.36% | -10% to 0% | ACCUMULATE | 87.7 | $90.76 |
| CAPR | -4.64% | -10% to 0% | STARTER | 57.8 | $14.98 |
| INBX | -7.49% | -10% to 0% | ACCUMULATE | 73.6 | $80.37 |
| LDOS | -10.27% | -20% to -10% | ACCUMULATE | 81.7 | $80.24 |
| PLAY | -17.61% | -20% to -10% | STARTER | 65.5 | $44.20 |

## Important Distinction

This is a performance overlay, not an execution report. PM approval does not prove that an order was submitted or filled. The exact PM fields and scan timestamps are preserved in the companion CSV, including the one-to-many relationship between scan observations and unique tickers.

