# Fill Diagnostics: Actual Equity Ledgers

## Scope and Method

Independent fill-ledger analysis, 2026-10-08. This supplements the main signal study; it is not a signal backtest or validation of the pre-registered strategy alternatives. Read AGENTS.md first. Only this report is written in the repository on Claude-Gpt-audit. No execution, database connection, API call, secret lookup, import of live trading modules, commit, or push.

Inputs were streamed line-by-line as JSON, retaining only the 91 target ledger records and small counters in memory. No whole-export load. Every line in both exports was parsed; no malformed JSON was encountered. All other collections were inventoried, not treated as fills.

| Input | Bytes | JSONL Records | Selected Records | SHA-256 |
|---|---:|---:|---:|---|
| C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/evidence.jsonl | 410145120 | 27734 | tf_trades:82 | ee63c707b43065220f9616ee14a80eec7fac4a0488dff75951ff4ced541184cf |
| C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/discovery.jsonl | 489313336 | 14063 | ll_tickets:9 | dff4c32237dad9c8dc7a26f1db100b99f78a6c58b67e4816b41850a7678b906b |

File size and modification time were unchanged during the full hashing/analysis pass. No duplicate (collection,doc_key) among selected records. Export completeness relative to source systems is not established. This is the exported snapshot, not verified current account truth.

## Record and Fill Counts

Broker categories use explicit markers: broker_base=public or broker=public is the Public live-marked ledger; the explicit paper-api.alpaca.markets URL is Alpaca PAPER, never live. Missing broker markers remain unknown even when a sell-fill repair names Alpaca. Public live account configuration was not independently queried.

| Ledger / Broker Category | Total Records | CLOSED Label | OPEN Label | Positive Filled Quantity | Positive Entry Fill Average | Supported Closed Fill-Price Returns |
|---|---:|---:|---:|---:|---:|---:|
| tf_trades / Public live-marked | 48 | 37 | 11 | 43 | 41 | 18 |
| tf_trades / Alpaca paper explicitly marked | 9 | 5 | 4 | 9 | 9 | 5 |
| tf_trades / missing broker marker | 25 | 25 | 0 | 25 | 25 | 1 |
| ll_tickets / Public live-marked | 9 | 6 | 3 | 9 | 9 | 6, overlapping tf_trades |
| Raw ledger total, NOT unique economic trades | 91 | 73 | 18 | 86 | 84 | 30 overlapping ledger returns |

All tf_trades instruments are fractional (34) or EQUITY (48); all nine ll_tickets are EQUITY, execution_origin=broker_fill. No manual/synthetic Lottery tickets or option contracts were present in the selected collections. A strategy name containing CALL does not change the actual stock instrument.

tf_trades fill-status breakdown:

- Public: 21 EXIT_FILLED/CLOSED, 11 FILLED/CLOSED, 11 FILLED/OPEN, 5 EXPIRED_BY_TERMINAL/CLOSED.
- Alpaca paper: 5 FILLED/CLOSED, 4 FILLED/OPEN.
- Missing marker: 25 FILLED/CLOSED.
- The five expired Public rows have no positive qty_total or filled_avg_price: TENX, BAH, FCEL, ABEO, PMAX. These are closed terminal order records, not realized trades.
- The remaining 77 tf_trades rows have positive filled quantity, but two imported Public closes, TENX and RZLT, have filled_avg_price=0. Their exit fills exist; their returns and cost basis do not.
- 32 Public CLOSED rows have positive filled quantity, versus 37 CLOSED labels. Of these, 18 support a strict fill-price return, 11 have cash-history-only exit evidence, two lack positive entry basis, and one (GRAL) lacks verified exit evidence.

Do not combine raw CLOSED counts with realized-trade counts. Fifteen tf_trades entries remain OPEN (11 Public,4 paper); no realized return is computed for them. The three OPEN ll_tickets are conflicting duplicates of CLOSED Public trades, discussed below.

## Quantity: Filled vs Requested vs Remaining

Use qty_total for filled tf_trades entry quantity and quantity for broker-filled ll_tickets. Use explicit sell filled quantity, never requested emergency_exit_order_qty, notional/price, allocation, or remaining holdings, to calculate realized slices.

- qty_total and filled_at are present on77 tf_trades; quantity and entry_filled_at are present on all9 tickets.
- All82 tf_trades have notional. This is not a substitute for executed entry cost. Public requests can be rounded to whole shares or incur different fill prices; SOAR's executed basis is17*0.3453=$5.8701, not an assumed $6.
- Mutable qty is present on34 legacy/paper rows:24 equal qty_total and10 differ. Source sync_positions_and_close_settled updates qty from current broker position, so differences are NOT evidence of incomplete entry fill or a requested-vs-filled ratio. Example paper GME qty=1.481764390 while total filled=4.939214631: using qty would omit prior exit slices.
- Original requested share quantities are not reliably retained in these exports. Public preflight estimatedQuantity appears on39 entry preflights and28 emergency preflights; those are estimates, not actual filled quantities. No defensible all-trades request fulfillment percentage is available.
- 14 missing-marker CLOSED rows still have qty_remaining>0. These are contradictory terminal states, not proof of unrealized exposure today and not proof of complete liquidation.
- All20 Public rows with positive emergency_exit_filled_qty have an explicit emergency order ID, status FILLED, and exit quantity equal to qty_total. Two of those20 cannot yield returns because entry average is zero. No duplicate emergency exit order IDs were found.
- All six supported legacy/paper tf_trades closes have explicit exit_fills, with summed sold quantity matching qty_total within max(1e-8,qty_total*1e-6). Their14 exit order IDs are unique. phases_hit is ignored in their arithmetic, avoiding counting phase summaries in addition to actual sell fills.

Source interpretation: backend/services/trade_floor.py (sync_positions_and_close_settled; repair_recent_closed_trade_truth), backend/services/trade_truth.py (sell-fill resolution), backend/services/lottery.py (record_filled_lottery_entry; close_filled_lottery_entry). These sources were read only; no service function was executed.

## Identifiable Lane Coverage

Lane identity comes from the filled record, not a join to future signals or same-ticker discoveries. CORE is a broad recorded family, not an identifiable sub-strategy. LEGACY_UNATTRIBUTED is explicitly not attributable. Missing IDs are not reconstructed from prices, symbol, or hindsight.

| Public tf_trades Recorded Strategy | All Records | Positive Filled Quantity | Filled CLOSED | Filled OPEN | Supported Closed Returns |
|---|---:|---:|---:|---:|---:|
| CORE | 14 | 12 | 4 | 8 | 3 |
| lottery_day2_continuation / DAY2_CONTINUATION | 10 | 9 | 9 | 0 | 5 |
| options_tactical_momentum_call / TACTICAL_MOMENTUM_CALL, actual stock | 1 | 1 | 1 | 0 | 1 |
| LEGACY_UNATTRIBUTED | 23 | 21 | 18 | 3 | 9 |
| Total | 48 | 43 | 32 | 11 | 18 |

Public specific named lanes are identifiable on10 positively filled economic entry rows:9 Day2 plus1 tactical-momentum CALL-tagged equity. Including CORE as broad identity gives22 of43 filled Public entries;21 remain legacy-unattributed. The five no-fill records must not inflate lane sample sizes.

All nine ll_tickets identify Day2 and mirror the same nine filled Day2 tf_trades entries. They add ZERO independent lane-entry samples. All nine explicitly marked paper tf_trades and all25 missing-marker tf_trades lack strategy_id/lane attribution; signal_combo may describe evidence but is not a recovered strategy lane. No Supernova, RedGreen, Catalyst Runner, Serial Runner or pharma-specific filled lane was identified from these ledgers.

The APLD equity carrying an options_tactical_momentum_call tag demonstrates historical route/attribution overlap; it remains an actual equity fill for this diagnostics report. Do not remove it merely because today's source excludes explicit OPTION route, or claim that a current routing reconstruction reproduces its historical decision.

## Supported Closed Returns First

Strict calculation:

~~~text
entry = positive recorded filled_avg_price (tf_trades)
        or entry_fill_price for broker-origin ll_tickets
sold_quantity = sum(actual documented exit fill quantities)
weighted_exit = sum(exit_fill_price_i * exit_fill_quantity_i) / sold_quantity
gross_return_pct = 100 * (weighted_exit / entry - 1)
gross_dollar_pnl = sum((exit_fill_price_i - entry) * exit_fill_quantity_i)
~~~

Closed trade requires positive entry average, positive fill quantity, actual documented sell prices/quantities, and full quantity reconciliation. Stored realized_pct, realized_pl_pct, raw_return_pct, haircut_return_pct, realized_pnl and generic exit_price do NOT supply the basis. Latest mark, limit price and notional/quantity are not substituted.

Important first-fill limitation: exports contain current ledger snapshots, not a chronological series of entry fills. These use the first available documented entry-average basis on that record, not a proven earliest individual execution. The Lottery hook can update cumulative averages. Imported cost-basis averages and broker repair fields remain exported evidence, not independently revalidated executions.

### tf_trades: 24 Supported Closed Records

Public rows use emergency_exit_fill_price and emergency_exit_filled_qty with broker_exit_verified; explicit FILLED emergency status/order IDs were checked separately. Paper/missing-marker rows use verified_alpaca_sell_fill and actual exit_fills. Weighted averages in the table are rounded only for display; calculations use recorded precision.

| Broker Marker | Ticker | Documented Exit Date UTC | Entry Average | Filled Quantity | Weighted Exit Fill | Gross Return | Gross P&L USD | Exit Slices |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Alpaca Paper | DDD | 2026-09-21 | 3.220000 | 14.186335403 | 3.626737 | 12.6316% | 5.770110 | 3 |
| Alpaca Paper | CACC | 2026-09-09 | 589.884000 | 0.069895098 | 595.182000 | 0.8981% | 0.370304 | 1 |
| Alpaca Paper | ECHO | 2026-09-22 | 85.484000 | 0.531912404 | 95.960000 | 12.2549% | 5.572314 | 3 |
| Alpaca Paper | AMRN | 2026-09-10 | 14.024000 | 0.610382201 | 14.270000 | 1.7541% | 0.150154 | 3 |
| Alpaca Paper | GME | 2026-09-15 | 18.590000 | 4.939214631 | 21.456000 | 15.4169% | 14.155789 | 3 |
| Public | SOAR | 2026-09-29 | 0.345300 | 17.000000000 | 0.303000 | -12.2502% | -0.719100 | 1 |
| Public | BAH | 2026-10-08 | 69.060000 | 0.086880000 | 72.500000 | 4.9812% | 0.298867 | 1 |
| Public | CRML | 2026-10-07 | 7.200000 | 0.833360000 | 7.175000 | -0.3472% | -0.020834 | 1 |
| Public | BKYI | 2026-09-30 | 2.498300 | 2.000000000 | 2.730000 | 9.2743% | 0.463400 | 1 |
| Public | MMS | 2026-10-08 | 56.000000 | 0.107140000 | 58.720000 | 4.8571% | 0.291421 | 1 |
| Public | SAIC | 2026-10-05 | 131.220000 | 0.045800000 | 124.340000 | -5.2431% | -0.315104 | 1 |
| Public | SDEV | 2026-10-02 | 4.500000 | 1.000000000 | 4.980000 | 10.6667% | 0.480000 | 1 |
| Public | RR | 2026-10-07 | 1.610000 | 3.726700000 | 1.620000 | 0.6211% | 0.037267 | 1 |
| Public | SDEV | 2026-10-05 | 4.800000 | 1.000000000 | 5.500000 | 14.5833% | 0.700000 | 1 |
| Public | RLAY | 2026-09-29 | 18.590000 | 0.322750000 | 17.290000 | -6.9930% | -0.419575 | 1 |
| Public | INLF | 2026-09-28 | 4.860000 | 1.234560000 | 4.000000 | -17.6955% | -1.061722 | 1 |
| Public | ARQQ | 2026-10-08 | 21.670000 | 0.276880000 | 19.510000 | -9.9677% | -0.598061 | 1 |
| Public | APLD | 2026-10-07 | 26.440000 | 0.226920000 | 23.780000 | -10.0605% | -0.603607 | 1 |
| Public | MMS | 2026-10-07 | 53.090000 | 0.113010000 | 55.660000 | 4.8408% | 0.290436 | 1 |
| Public | ALMU | 2026-10-07 | 13.000000 | 0.461530000 | 12.670000 | -2.5385% | -0.152305 | 1 |
| Public | FLWS | 2026-09-29 | 3.580000 | 1.675970000 | 2.660000 | -25.6983% | -1.541892 | 1 |
| Public | LDOS | 2026-10-02 | 133.010000 | 0.045110000 | 119.330000 | -10.2849% | -0.617105 | 1 |
| Public | LOVE | 2026-09-29 | 16.040000 | 0.374060000 | 15.530000 | -3.1796% | -0.190771 | 1 |
| Missing Marker | NXH | 2026-08-24 | 4.610000 | 4.926247288 | 3.990000 | -13.4490% | -3.054273 | 1 |

The date column is exported exit evidence date, not necessarily the exact broker execution instant. Alpaca exit_fills include14 filled_at timestamps. Public broker_exit_filled_at exists on12 closed rows but is written at reconciliation time by source; eight older emergency fills used here have only closed_at. Public timestamps therefore prove ledger dating, not tick-accurate broker sell time.

### ll_tickets: Six Supported Closed Records, Not Six More Trades

All six have entry_fill_price, exit_fill_price, exit_quantity=quantity and quantity_remaining=0, plus closed_at/last_exit_at. No full exit_order_fills history is exported. For these six, equal quantities support the ledger's full-exit claim; multiple historical partial exits cannot independently be ruled out beyond that snapshot.

| Ticker | Closed Date UTC | Entry Fill | Exit Fill | Exit Quantity | Gross Return | Counting Treatment |
|---|---|---:|---:|---:|---:|---|
| TGE | 2026-09-30 | 1.420000 | 1.400000 | 4.225350000 | -1.4085% | Additional price evidence; already same economic entry |
| SDEV | 2026-10-05 | 4.800000 | 5.500000 | 1.000000000 | 14.5833% | Duplicate of supported tf_trades row |
| INLF | 2026-09-28 | 4.860000 | 4.000000 | 1.234560000 | -17.6955% | Duplicate of supported tf_trades row |
| BKYI | 2026-09-30 | 2.498300 | 2.730000 | 2.000000000 | 9.2743% | Duplicate of supported tf_trades row |
| SDEV | 2026-10-02 | 4.500000 | 4.980000 | 1.000000000 | 10.6667% | Duplicate of supported tf_trades row |
| SOAR | 2026-09-29 | 0.345300 | 0.303000 | 17.000000000 | -12.2502% | Duplicate of supported tf_trades row |

TGE: ll_tickets exit_fill_price=1.40 supports -1.4084507% gross and -$0.084507 gross at4.22535 shares. Matching tf_trades instead reports broker_exit_price=1.393967, derived from net cash $5.89/quantity; that is NOT the actual fill price. The duplicate cannot be counted as another entry, but it supplies fill-price evidence absent from tf_trades.

Therefore, 24 tf_trades supported return records plus six ticket return records collapse to 25 distinct economic entries with some supported fill-price evidence: 19 Public, 5 explicitly marked paper, 1 missing broker. This does not turn the 11 cash-history-only tf_trades rows into gross-fill returns; only TGE gains corroborating ticket price evidence.

## Public Cash-History Evidence Is a Different Basis

19 Public rows contain realized_pnl_source=public_history_exact_quantity_match or public_history_unattributed_round_trip. Eight also have actual emergency fill prices; use actual fills for gross returns. Eleven lack a positive actual emergency fill price: TGE, BFRG, AEHL, PMAX, IPSC, PLAY, GOLD, INBX, MRLN, CAPR, SHOE.

The source history reconciliation constructs broker_exit_price from sell netAmount / quantity and often constructs stored realized_pnl from net proceeds minus requested notional. Consequently broker_exit_verified=true and a displayed realized percentage alone do not establish an actual fill-to-fill return or complete paid-fee basis.

Examples:

- BKYI actual exit2.73 versus cash-equivalent2.72; SDEV's earlier trade4.98 versus4.97.
- SOAR actual exit0.303 versus cash-equivalent0.301765.
- TGE actual ticket exit1.40 versus cash-equivalent1.393967.
- These differences must not be automatically called fees: cash rounding, settlement adjustments and undisclosed charges are not decomposed in the exports.
- MRLN has unattributed imported cash round-trip evidence, not a documented actual sell-price field.
- TENX and RZLT have explicit emergency sell fills but entry average zero. Even with broker_exit_verified=true, neither a realized price return nor economic cost basis is supported. Do not fall back to requested limits, portfolio marks or stored realized percentages.
- GRAL CLOSED at2026-10-08 19:11 UTC lacks verified sell evidence while a separate GRAL row is OPEN. A closed label does not imply an actual realized trade.

No extra return table is manufactured from the 11 cash-only rows. Net cash histories are retained as a separate evidence tier, not summed into gross-fill P&L.

## Duplicate, Partial-Exit and Legacy Warnings

1. All9 ll_tickets match exactly one Public tf_trades by broker_order_id versus public_order_id/order_id, with equal entry quantity and average. Sum neither ledger wholesale nor add matching tickets to tf_trades. There were no duplicate tf_trades entry order IDs among IDs present, but some imported rows lack entry IDs.
2. BFRG, PMAX and AEHL are OPEN in ll_tickets yet CLOSED with history-confirmed exit in tf_trades. Each ticket has broker_exit_unconfirmed markers. They are three stale/conflicting statuses, not three additional open positions or new strategy samples.
3. 12 missing-marker rows have nonempty phases_hit, but only NXH in that category has explicit repaired sell fills. Bare phase prices may be trigger/mark fallbacks: legacy _market_sell_and_record can use supplied exit_price when immediate average-fill price is absent. No return is recomputed from phases_hit alone.
4. DDD, ECHO, AMRN and GME each have three explicit exit fills; CACC and NXH have one. Their14 sells are used once, instead of adding generic exit_price or phase3 totals again. Order of submitted phase names and broker fill times can differ; broker fill quantities/prices are the evidence.
5. Four paper plus11 Public tf_trades remain OPEN. Do not realize mark-to-market values or extrapolate future exits. Missing complete historical partial exits prevents certifying all-account realized P&L from these snapshots.
6. BBBY, NHX and NXH missing-marker rows share the same entry average4.61 and quantity4.926247288. Their separate entry IDs do not prove separate economic holdings; symbol alias/rename or reconciliation duplication is possible. Do not aggregate the missing-marker ledger into either paper or live books without identity/corporate-action history.
7. Some Public rows carry unverified_no_alpaca_sell_fill from legacy repair despite later Public history verification. That unrelated broker flag neither validates nor invalidates the actual Public execution basis on its own.
8. Neither export contains tf_phase_outcomes or a comprehensive broker execution tape. Phase information is embedded only in some tf_trades. Raw orders, full paid charges, cancelled/replaced sells and all intermediate partial fills are not proven included.

## Fees and Financial Aggregation

Actual paid fee totals are documented on ZERO of82 tf_trades and ZERO of9 tickets. There is no actual_fees_usd. Entry preflight estimated fee fields occur on39 Public records; normalized estimated economics on10; emergency preflight estimates on28. They are estimates, with null components common, not paid charges. ll_tickets has no fee fields. Its fixed2.5-percentage-point haircut is synthetic, not actual transaction expense.

No supported all-trades NET financial P&L, live account return, equity curve or merged live+paper total is reported. No fees are silently assumed zero.

The following are narrowly scoped GROSS arithmetic subtotals, not total strategy/account profits:

| Non-Overlapping Supported Subset | Closed Entries | Gross Fill-Based P&L USD | What Is Excluded |
|---|---:|---:|---|
| Public tf_trades emergency fills with positive entry basis | 18 | -3.678685 | All ll_tickets mirrors including TGE; cash-only closes; zero basis; GRAL unverified close; OPEN positions; unexported executions; actual fees |
| Explicitly marked Alpaca PAPER, all documented slices per selected close | 5 | +26.018672 | Four OPEN paper rows, missing-marker ledger, all unexported activity and fees |

These subtotals are permitted only for the selected records: all18 Public emergency sell IDs are distinct; paper's13 sell IDs are distinct; full selected exit quantities reconcile to filled entry quantity. Generic phase summaries and ll_tickets are not added. Public subset includes four imported entries without entry order IDs; quantity/price evidence supports arithmetic, but a complete acquisition-lot history is not established.

The missing-marker NXH per-trade gross loss (-$3.054273) is shown in its row, NOT added to paper/live subtotals because broker and economic identity are unresolved. TGE is likewise shown as ticket evidence but not added to the conservative Public tf_trades subtotal. Adding excluded rows, cash-net records, duplicate tickets or missing phase activity would invalidate this scope.

These sums are not return percentages on initial account capital. Different holding dates, cash flows, open holdings, missing fees and incomplete trade/phase exports make an account-level aggregate unsupported.

## Conclusions and Exact Output

- Raw target ledger records:82 tf_trades +9 ll_tickets; positive-quantity tf entries77; Public filled entries43, paper9, missing marker25.
- Strict supported tf closed fill-price returns24; ticket closed returns6; after exact mirror matching25 economic entries have some supported closed fill-price evidence.
- Only10 Public positively filled entries have a specific named strategy lane (9 Day2,1 tactical-momentum-tagged equity);12 additional filled entries have broad CORE identity. Lottery tickets add no independent entries.
- Full realized-account profitability remains unsupported because gross/net bases differ, actual fees are undocumented, three ticket states conflict, legacy closes/partial exits are incomplete, some markers/bases/IDs are missing, and all phase/broker execution records are not included.
- Only written path: C:/Users/tcase/OneDrive/Documents/Case Cap/CaseCapitalTradingTerminal-audit/docs/equity-backtest-2026-10-08/fill-diagnostics.md. The main signal-study files and strategy-reconstruction.md were not edited.
