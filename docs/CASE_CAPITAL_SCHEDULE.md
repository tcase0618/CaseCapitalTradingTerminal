# Case Capital Terminal Schedule

Generated from the scheduler definitions at commit `8ee0b39`. All times are Eastern Time (ET). The scheduler is market-calendar aware: market-session jobs remain in standby on weekends and exchange holidays, while research, health, reconciliation, and reporting jobs continue according to their own cadence.

## Market-session scans

| Time | Job | Scope | Weekend / holiday behavior |
|---|---|---|---|
| 00:00 | Coordinated stock scan | Core plus PM-routable strategy fan-out | Standby |
| 08:00 | Coordinated stock scan | Core plus PM-routable strategy fan-out | Standby |
| 09:35 | Options pre-open scan and execution preflight | Options desk | Standby |
| 10:00 | Coordinated stock scan and options-only scan | Core, strategy fan-out, options | Standby |
| 12:00 | Coordinated stock scan | Core plus PM-routable strategy fan-out | Standby |
| 15:00 | Coordinated stock scan | Core plus PM-routable strategy fan-out | Standby |
| 18:30 | Coordinated stock scan | Core plus PM-routable strategy fan-out | Standby |

## Intraday monitoring

| Cadence | Job | Scope |
|---|---|---|
| Every 1 minute | Order queue flush | Pending execution lifecycle |
| Every 5 minutes | Position monitor and reconciliation | Alpaca positions, open orders, risk state |
| Every 5 minutes | Kronos forecast refresh | Market forecast snapshot |
| Every 5 minutes | Lottery active monitor | Open lottery positions and active setups |
| Every 5 minutes | Execution authority refresh | Tradeability and broker readiness |
| Every 5 minutes | Alert checks | Alert conditions and outbound dispatch |
| Every 10 minutes | Pharma catalyst shock sweep | Pharma research alerts; included in consolidated scan reporting |
| Every 15 minutes, 09:00-16:00 weekdays | Options flow | Options research and candidate context |
| Every 30 minutes, 09:00-16:30 weekdays | Regime gate | Market regime state |
| Every 10 minutes | Scheduler watchdog | Stale-source detection and bounded repair |

## Daily and weekly operations

| Time / cadence | Job | Scope |
|---|---|---|
| 09:30 weekdays | Kronos morning forecast | Market brief and forecast ledger |
| 17:02 Mon-Thu | Daily operations report | Performance, routing, and system health |
| 21:02 Friday | Weekly operations report | Weekly performance and quality review |
| 23:00 and 02:00 | P&L refresh | Returns and settlement tracking |
| 02:00 daily | Database backup | Operational recovery |
| 02:05 daily | Stale-order sweep | Order lifecycle cleanup |
| Hourly | Research lab snapshot | Research-only analytics |
| Hourly | Strategy screeners refresh | Cached scanner data |

## Weekly learning and governance

| Time | Job |
|---|---|
| Sunday 02:00 | Learning cycle |
| Sunday 02:20 | Lottery learning cycle |
| Sunday 03:00 | Trade-floor recalibration |
| Sunday 19:50 | Truth review |

## Operating rules

- The scheduler uses the exchange calendar for stock-market session decisions. A weekend `STANDBY` state is expected and is not a data failure.
- A source is not considered fresh merely because a QC cache was refreshed. Health age is calculated from the provider evidence timestamp.
- Alpaca is the execution and account-data authority. A failed or stale Alpaca probe blocks order scopes; it does not create orders or silently fall back to an unverified source.
- The HTTP VPS deployment does not register a Telegram inbound webhook. Outbound consolidated reports remain available when Telegram credentials are configured.
- No scheduled job submits an order while the market is closed. No scan or health check in this schedule bypasses the execution gate.
