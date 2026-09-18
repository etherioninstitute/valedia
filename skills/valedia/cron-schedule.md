---
description: >
  Complete cron schedule for Valedia. 24 jobs on Mac Studio. All times UTC.
  Updated 2026-09-17 (Slice 7: sports :00 / allocator :20 stagger).
---

# cron-schedule (24 jobs)

## Trading engines (high frequency)

| Schedule | UTC | Job | Script | Purpose |
|----------|-----|-----|--------|---------|
| */30 * * * * | Every 30min | LATENCY ARB | arb_run.py | BTC/ETH spot vs Kalshi strikes (dry-run) |
| 30 * * * * | Every hour | SETTLEMENT | settle_run.py | Check bet results, Signal profit alerts |
| 0 */2 * * * | Every 2h at :00 | SPORTS ENGINE | sports_run.py | Scan+bet 70-85c favorites (live) |
| 20 */4 * * * | Every 4h at :20 | MASTER ALLOCATOR | allocator_run.py | Cross-strategy capital allocation (staggered vs sports — Slice 7, no double-bet race) |
| 0 */6 * * * | Every 6h | LEDGER RECONCILE | reconcile_run.py | Kalshi settle + spot UNRECONCILED walk (GET HTTP off; Burton flip: `export SPOT_RECONCILE_HTTP=1` or `SPOT_RECONCILE_HTTP = True`) |
| 0 */6 * * * | Every 6h | QUANT ENGINE | quant_run.py | LMSR+EV+Kelly quant scan |
| 30 */6 * * * | Every 6h | MARKET SCANNER | scanner_run.py | 600+ Kalshi markets edge scoring |
| 0 */6 * * * | Every 6h | WHALE MONITOR | whale_copier.py | Whale wallet monitoring |
| 30 */6 * * * | Every 6h | DISCOVER | dynamic_universe.py | Asset universe expansion |

## Intelligence (daily, overnight)

| Schedule | UTC | Job | Script | Purpose |
|----------|-----|-----|--------|---------|
| 0 1 * * * | 1:00 AM | MIROFISH SWARM | mirofish_runner.py | 5-agent sentiment swarm |
| 0 1 * * * | 1:00 AM | ML RETRAIN | ml_kernel_retrain.py | 8 GradientBoosting models |
| 0 2 * * * | 2:00 AM | INTEL+SENTIMENT | intel_sentiment.py | Finnhub + news sentiment |
| 0 2 * * * | 2:00 AM | ASI SCAN | asi_scan.py | Full ASI pipeline |
| 15 2 * * * | 2:15 AM | INSIDER | insider_mirror.py | SEC Form 4 insider buys |
| 30 2 * * * | 2:30 AM | HEDGE INTEL | hedge_fund_intel.py | 13F institutional positions |
| 0 3 * * * | 3:00 AM | FUND CHECK | fund_pipeline.py | Mercury fund status |
| 0 3 * * * | 3:00 AM | MEMORY SYNC | rsync | NIMROD_SSD -> VPS memory |

## Market hours (Mon-Fri)

| Schedule | UTC | Job | Script | Purpose |
|----------|-----|-----|--------|---------|
| 35 6 * * 1-5 | 6:35 AM | BRAIN OPEN | unified_brain.py | Market open scan |
| 0 9 * * 1-5 | 9:00 AM | BRAIN MIDDAY | unified_brain.py | Midday check |
| 45 12 * * 1-5 | 12:45 PM | BRAIN CLOSE | unified_brain.py | Market close |

## Reports

| Schedule | UTC | Job | Script | Purpose |
|----------|-----|-----|--------|---------|
| 0 7 * * * | 7:00 AM | MORNING BRIEF | kalshi_alert.py | Daily briefing |
| 0 15 * * * | 3:00 PM | EOD PORTFOLIO | portfolio_monitor.py | End of day status |
| 0 23 * * * | 11:00 PM | P&L TRACKER | pnl_run.py | Settlement + Ollama AI audit |

## Signal output

Cron runner (cron_runner_mac.sh) NEVER sends to Signal directly.
should_send() always returns 1 (suppress all).
Only sports_engine.py and settle_run.py send via signal_send.py:
- Invested: when bets placed
- Profit: when bets settle
