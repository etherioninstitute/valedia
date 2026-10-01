---
description: >
  Signal messaging protocol. Every money figure is code-built from a named source,
  re-read and verified (signal_verify) before send; anything unverifiable is withheld.
  Model-written text never reaches Signal. Everything else goes to local logs.
---

# signal protocol

## Rule

Burton's Signal gets ONLY the messages below. Every figure names its source (Kalshi
broker, fee-true ledger, or a labelled model), is re-read from that source and must
match (`signal_verify.deliver`). Money format everywhere: `signal_verify.money` —
cents rounded half away from zero, sign by the rounded value (never `-$0.00`),
negatives with a real minus (`−$5.00`), losses add `(net loss)`; sums use Decimal.
Any mismatch / stale / unreadable source → ONE figure-free
`Report withheld (<kind>): <reason>. Unverified figures not sent; see data/signal/verify_log.jsonl.`
(details in data/signal/verify_log.jsonl). Every verified message ends with a
`✓ verified: …` footer (AI answers: `✓ figures from broker/ledger only`).
ALL other output goes to ~/.openclaw/logs/valedia-cron.log.

## Senders and exact formats

### Profit — settle_run.py (hourly :30)
```
Profit (settled since last report): +$4.96 (1W/0L)
Realized P&L (all-time, ledger, all venues, fee-incl.): +$6.06 (7W/1L)
```
- Id-based: reports exactly the valid live settlements not yet in the sent-record
  (data/signal/profit_sent_ids.jsonl + backup in models/), under an exclusive lock
  (data/signal/settle.lock). No cursor. Never reports a settlement twice.
- Bad ledger rows (truncated/non-object line, missing/NaN/garbage P&L or win flag,
  missing/garbage/future time, orphan, conflicting duplicate settlement) never block
  valid ones; the all-time line is then replaced by
  `⚠ N ledger row(s) unparseable — all-time total omitted`. With nothing valid to
  report, a new bad-row set sends once: `⚠ N ledger row(s) unparseable — their figures withheld until fixed`.
- Delivery unknown (relay timeout) → next run re-sends once with
  `(re-sent: previous delivery unconfirmed) N of these settlement(s)`; confirmed
  failure → retried next run.
- Sent-record unreadable → `Profit report paused — sent-record unreadable` (fail closed).
- Silent when nothing new settled.

### 11pm P&L — pnl_run.py (23:00)
```
💰 Valedia P&L — 2026-10-01
Open-position cost (Kalshi, broker): $40.00
Realized P&L (all-time, ledger, all venues, fee-incl.): −$10.34 (net loss) · 1W/1L
Settled last 24h (ledger): −$12.34
```
- All-time line → `omitted — N ledger row(s) unparseable|conflicting duplicate settlement`
  when the ledger has bad rows; 24h line → `omitted — N row(s) lack times` when any
  settlement has no usable time. `⚠ STALE: ledger …` header when the ledger is stale,
  future-dated or undated; `⚠ ledger has N open Kalshi entries vs broker M positions (not reconciled)`.

### Sports fills — sports_engine.py (every 2h, real executions only)
```
Sports fills cost incl. fees (ledger): $8.14 on 2 games
```
Orders that end UNRECONCILED / FAILED: `Kalshi sports: N order(s) unconfirmed — figures withheld until reconciled` (no amounts, once per order set).

### 7am brief — kalshi_alert.py (only when there is positive modeled upside)
```
Valedia 7am brief — Kalshi
Open-position cost (Kalshi, broker): $2.50
Modeled if-all-win: +$1.14 (2 picks that fit cash, fee-incl. sim) — not profit, not EV, not fills
```

### AI answers — claude_router.sh / claude_bridge.py --router|--signal
Code-built facts block + one fixed line; the model's wording is never sent:
```
Facts (code-generated; each source read twice):
Cash (Kalshi, broker): $123.45
Open-position cost (Kalshi, broker): $2.50
Open positions (Kalshi, broker): 1
Realized P&L (all-time, ledger, all venues, fee-incl.): +$3.00 (1W/0L)
AI answer not sent to Signal (model wording cannot be verified) — full text saved: <path>
✓ figures from broker/ledger only
```
Unavailable figures read `omitted — <reason>`, never `$0.00`.

### Ops / legacy (no figures)
- watchdog.py: `WATCHDOG ALERT` + fixed sentences; any number in free text → `[n]`.
- scripts/cron_runner.sh (VPS legacy): only `Valedia job <LABEL> finished OK` /
  `Valedia job <LABEL> FAILED (see cron log)` (label from the install_cron.sh allowlist).
- btc_day_reminder.py: date-guarded one-off reminder, no figures.

## Implementation

signal_send.py: transport only, tri-state (True delivered / False confirmed failure /
None unknown); escapes `$` as `USD `. Target uuid:47bd6dd5-2c96-4514-9541-acd8ed013612.
Route: NIMROD -> SSH -> VPS -> openclaw message send -> Signal.
cron_runner_mac.sh: should_send() always returns 1 (never sends). Scripts send only
through signal_verify.

OpenClaw's own chat replies are model-written and NOT covered — see
skills/valedia/claude-bridge.md TODO(Burton).
