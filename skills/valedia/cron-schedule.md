---
description: >
  Complete cron schedule for Valedia. 28 jobs on Mac Studio. Cron fields are the
  Mac's LOCAL clock (America/Los_Angeles, PT); UTC shown in parentheses (PDT, UTC-7).
  Source of truth is scripts/install_cron.sh; scripts/test_cron_schedule.py enforces this doc matches it.
---

# cron-schedule (28 jobs)

**Timezone.** The Mac clock is PT. Every `Schedule` column below is the literal crontab
field (Mac-local PT). The parenthesised UTC is for PDT (UTC-7, Mar-Nov); during PST (UTC-8)
add one hour. `+1d` = falls on the next UTC day. Earlier revisions of this doc mislabelled
these PT times as UTC.

**Source of truth.** `scripts/install_cron.sh` (do not run it on the live machine; Burton only).
This doc mirrors it row for row. Edit the installer first, then this doc, then run
`python3 scripts/test_cron_schedule.py`. Jobs run through `cron_runner_mac.sh <script> "<job>"`
unless the Script column is a `.sh` (those are invoked directly).

## Trading engines (high frequency)

| Schedule | Time PT (UTC) | Job | Script | Purpose |
|----------|---------------|-----|--------|---------|
| `*/30 * * * *` | every 30 min | LATENCY ARB | arb_run.py | BTC/ETH spot vs Kalshi strikes (dry-run only; never orders) |
| `30 * * * *` | every hour at :30 | SETTLEMENT | settle_run.py | Check bet results, Signal profit alerts |
| `0 */2 * * *` | every 2h at :00 | SPORTS ENGINE | sports_run.py | Scan+bet 70-85c favorites (live) |
| `20 */4 * * *` | every 4h at :20 | MASTER ALLOCATOR | allocator_run.py | Cross-strategy capital allocation (staggered vs sports, Slice 7, no double-bet race) |
| `0 */6 * * *` | every 6h at :00 | LEDGER RECONCILE | reconcile_run.py | Kalshi settle + spot UNRECONCILED walk (GET HTTP off; Burton flip: `export SPOT_RECONCILE_HTTP=1` or `SPOT_RECONCILE_HTTP = True`) |
| `45 6 * * *` | 6:45 AM PT (13:45 UTC) | SHADOW PARITY | parity_run.py | Report-only Intent vs POST compare (7d). Writes `models/shadow_parity_report.json`. Never POSTs / never flips flags. |
| `0 */6 * * *` | every 6h at :00 | QUANT ENGINE | quant_run.py | LMSR+EV+Kelly quant scan |
| `30 */6 * * *` | every 6h at :30 | MARKET SCANNER | scanner_run.py | 600+ Kalshi markets edge scoring |
| `0 */4 * * *` | every 4h at :00 | WHALE MONITOR | whale_copier.py | Whale wallet monitoring |
| `30 */6 * * *` | every 6h at :30 | DISCOVER | dynamic_universe.py | Asset universe expansion |

## Intelligence (daily, overnight PT)

| Schedule | Time PT (UTC) | Job | Script | Purpose |
|----------|---------------|-----|--------|---------|
| `0 1 * * *` | 1:00 AM PT (08:00 UTC) | MIROFISH SWARM | mirofish_primary.sh | Real swarm via mirofish_runner.py (intel_refresh fallback); writes the `mirofish_ready` marker the BRAIN OPEN gate waits on |
| `0 1 * * *` | 1:00 AM PT (08:00 UTC) | ML RETRAIN | ml_kernel_retrain.py | 8 GradientBoosting models |
| `0 2 * * *` | 2:00 AM PT (09:00 UTC) | INTEL+SENTIMENT | intel_sentiment.py | Finnhub + news sentiment |
| `0 2 * * *` | 2:00 AM PT (09:00 UTC) | ASI SCAN | asi_scan.py | Full ASI pipeline |
| `15 2 * * *` | 2:15 AM PT (09:15 UTC) | INSIDER | insider_mirror.py | SEC Form 4 insider buys |
| `30 2 * * *` | 2:30 AM PT (09:30 UTC) | HEDGE INTEL | hedge_fund_intel.py | 13F institutional positions |
| `0 3 * * *` | 3:00 AM PT (10:00 UTC) | FUND CHECK | fund_pipeline.py | Mercury fund status |

## Market hours (Mon-Fri, PT)

| Schedule | Time PT (UTC) | Job | Script | Purpose |
|----------|---------------|-----|--------|---------|
| `35 6 * * 1-5` | 6:35 AM PT (13:35 UTC) | BRAIN OPEN | brain_open_gated.sh | Market open scan; waits up to 20 min for MiroFish, then runs unified_brain.py "BRAIN OPEN" (idempotent per day) |
| `0 9 * * 1-5` | 9:00 AM PT (16:00 UTC) | BRAIN MIDDAY | unified_brain.py | Midday check |
| `45 12 * * 1-5` | 12:45 PM PT (19:45 UTC) | BRAIN CLOSE | unified_brain.py | Market close |

## Reports and crypto

| Schedule | Time PT (UTC) | Job | Script | Purpose |
|----------|---------------|-----|--------|---------|
| `0 7 * * *` | 7:00 AM PT (14:00 UTC) | MORNING BRIEF | kalshi_alert.py | Daily briefing |
| `4 7 * * *` | 7:04 AM PT (14:04 UTC) | WEATHER MORNING GO | weather_morning_go.sh | Weather morning go; Valedia runtime decides WEATHER_LIVE from preflight, never unfences oil |
| `0 15 * * *` | 3:00 PM PT (22:00 UTC) | EOD PORTFOLIO | portfolio_monitor.py | End of day status |
| `0 23 * * *` | 11:00 PM PT (06:00 UTC +1d) | P&L TRACKER | pnl_run.py | Settlement + Ollama AI audit |
| `0 19 * * *` | 7:00 PM PT (02:00 UTC +1d) | BRAIN CRYPTO | unified_brain.py | Crypto 24/7 scan |

## Weekly

| Schedule | Time PT (UTC) | Job | Script | Purpose |
|----------|---------------|-----|--------|---------|
| `0 17 * * 0` | Sun 5:00 PM PT (00:00 UTC +1d) | DCA | dca_module.py | Weekly DCA module |
| `0 12 * * 5` | Fri 12:00 PM PT (19:00 UTC) | REBALANCE | rebalancer.py | Weekly rebalance |
| `50 4 * * 0` | Sun 4:50 AM PT (11:50 UTC) | SELF-EVOLVE WEEKLY | ../self_evolve/weekly_run.py | Policy-lab proposals per category (offline, no LLM/network). Writes `recommendations/proposals/SELF-EVOLVE-<date>.md/.json` + labs in `~/.openclaw/state/policy_lab/<date>/`. PROPOSALS ONLY: never live config, crontab, models/ or orders; Burton approves. Exit 0 even when nothing qualifies |

## Removed (deprecated, not installed)

Commented out in `install_cron.sh`; the scripts are renamed `*.DEPRECATED` in `scripts/integrations/`.

- `emerging_crypto_scanner.py` "EMERGING" (discovery is now DISCOVER / dynamic_universe.py)
- `sentiment_scanner.py` "SENTIMENT" (replaced by INTEL+SENTIMENT / intel_sentiment.py)
- `alpha_intelligence.py` "ALPHA INTEL" (sources live in INSIDER, HEDGE INTEL, WHALE MONITOR)

## Not managed by the installer

- MEMORY SYNC (rsync NIMROD_SSD -> VPS memory, formerly listed at 3:00 AM): no repo script or exact command exists, so it is not installed here. Add the line to the installer (and a row here) if it is still wanted.
- Live-only jobs that exist on the Mac but are not in the installer: `kalshi_audit.py` (23:30), `intel_refresh.py` (4h), `watchdog.py` (`*/30`). Installing over the live crontab would drop the runner-based ones (see the warning in the installer).

## Shadow parity report (Slice 16)

File: `~/.openclaw/workspace/models/shadow_parity_report.json`  
CLI: `python3 scripts/integrations/nautilus_shadow_parity.py --window 7d`  
Cron wrapper: `parity_run.py` (always exit 0; report-only). Cron time is PT (6:45 AM PT = 13:45 UTC).

| Field | Meaning |
|-------|---------|
| `ok` | No dangerous mismatches in the window |
| `diffs` | Per-row advisory / stored compare mismatches |
| `dangerous` | Fail-closed: side, instrument, size, venue, signed, hook, leverage, dispatchable, live_flag, writer |
| `buckets.*.compare_ok` | Rows that match Valedia POST bodies |
| `ignored.allocator_coinbase` | Allocator Coinbase is not cutover evidence |
| `cutover_ready` | 7d matching evidence. **Does not enable NT / writer / GET HTTP / AUTOEXEC** |
| `flags` | Must stay `NAUTILUS_LIVE=false`, writers `valedia` or `none` |

Manual cron (do not run `install_cron.sh` on live):

```
45 6 * * * /Users/burtonchatelain/.openclaw/workspace/scripts/cron_runner_mac.sh parity_run.py "SHADOW PARITY" 2>&1
```

## Signal output

Cron runner (cron_runner_mac.sh) NEVER sends to Signal directly.
should_send() always returns 1 (suppress all).
Only sports_engine.py and settle_run.py send via signal_send.py:
- Invested: when bets placed
- Profit: when bets settle
