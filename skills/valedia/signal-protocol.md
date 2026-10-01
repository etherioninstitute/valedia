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

### Which settlements count — ledger_view.py (shared by Profit, 11pm, AI facts)
One selection, so the senders never disagree: live + reconciled settlements only (not
paper / sandbox / backfill / shadow / UNRECONCILED, on the settlement or its entry);
canonical entry id = `str(entry_id)`; the FIRST valid settlement per entry counts
(identical duplicates ignored, a differing one = conflict). Flagged rows (unparseable
line / non-object row, live orphan, missing / NaN / garbage P&L or win flag, missing /
garbage booking time, conflicting duplicate, and future-dated rows — any of logged_at /
ts / settled_at / settlement_ts more than 5 min ahead) are excluded, and EVERY sender
omits the all-time total with the same text: `N ledger row(s) unparseable / future-dated /
conflicting duplicate settlement`. `now` is taken after the ledger read.

### Profit — settle_run.py (hourly :30)
```
Profit (settled since last report): +$4.96 (1W/0L)
Realized P&L (all-time, ledger, all venues, fee-incl.): +$6.06 (7W/1L)
```
- Id-based: reports exactly the valid settlements not yet in the sent record
  (data/signal/profit_sent_ids.jsonl + backup models/settle_profit_sent_ids.jsonl, one
  JSON id per line), under an exclusive lock. Never reports a settlement twice.
- Rollout / bootstrap (no sent record yet): if the old cursor
  models/settle_ftl_signal_cursor.txt is present, settlements booked at or before it
  count as sent and everything after it is reported. No (or garbage / future) cursor:
  every existing id becomes the baseline only after one figure-free notice
  `Profit reporting (re)initialized — earlier settlements are included in the 11pm all-time total`
  (history is never marked silently; a failed notice marks nothing and retries). The
  cursor file is rewritten after each confirmed report, so if both records are ever
  lost or emptied the bootstrap reseeds from it.
- Ledger missing / unreadable: `Profit report paused — ledger unreadable`, no baseline
  recorded (bootstrap deferred until the ledger is readable).
- Bad rows never block valid ones; the all-time line becomes
  `⚠ <reason> — all-time total omitted`. With nothing valid to report, a new bad-row set
  sends once: `⚠ <reason> — their figures withheld until fixed`.
- Delivery (write-ahead): the ids are appended + fsync'd to
  profit_unconfirmed_ids.jsonl BEFORE the send. Confirmed delivery moves them to the sent
  record; a confirmed failure removes the write-ahead entry (retried next run, no label);
  unknown (relay timeout, ssh exit 255, exception, or a crash after send) leaves it, so
  the next run re-sends once with `(re-sent: previous delivery unconfirmed) N of these
  settlement(s)`. After two unknown deliveries of the same ids: one figure-free
  `Profit report delivery unconfirmed twice — see the 11pm report`, then marked sent.
- Record write fails after a confirmed delivery: no "withheld" notice; logged, the ids
  kept as pending_sent in settle_state.json and recording retried next run.
- Record robustness: appends first terminate an unterminated last line; bad / duplicate
  lines rewritten away; one unreadable copy is tolerated while the other is readable; a
  last line cut mid-write in both copies is recovered as sent (logged).
- Sent record unreadable (both copies): `Profit report paused — sent-record unreadable ·
  N settlement(s) waiting`. Paused notices (ledger unreadable, sent-record unreadable /
  unwritable, no lock available, settle lock held too long) go out at most once per day.
- Lock: flock on data/signal/settle.lock (held → exit quietly). flock unsupported
  (ENOLCK) or the lock path unusable → O_EXCL pid file settle.lock.pid (stale after 20
  min or when its pid is dead). No lock possible → `Profit report paused — no lock
  available` once per day.
- Silent when nothing new settled.

### 11pm P&L — pnl_run.py (23:00)
```
💰 Valedia P&L — 2026-10-01
Open-position cost (Kalshi, broker): $40.00
Realized P&L (all-time, ledger, all venues, fee-incl.): −$10.34 (net loss) · 1W/1L
Settled last 24h (ledger): −$12.34
```
- Uses the shared selection. Flagged rows → both lines `omitted — <reason>`. 24h window:
  `now − 24h < settled_at ≤ now + 5 min` (settled_at / settlement_ts / ts / logged_at);
  a present-but-garbage settlement time → `omitted — N row(s) lack times`.
  `⚠ STALE: ledger …` header when the ledger is stale, future-dated or undated;
  `⚠ ledger has N open Kalshi entries vs broker M positions (not reconciled)`.

### Sports fills — sports_engine.py (every 2h, real executions only)
```
Sports fills cost incl. fees (ledger): $8.14 on 2 games
```
Orders that end UNRECONCILED / FAILED: `Kalshi sports: N order(s) unconfirmed — figures withheld until reconciled` (no amounts, once per order set).
Corrupt sports log (invalid JSON): the file is moved to
`models/sports_engine_log.corrupt-<ts>.json` (history never overwritten), a fresh log is
started, and one figure-free notice is sent: `Sports log corrupt — moved aside (history
kept), started fresh; sports figures come from the ledger only`.

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

signal_send.py: transport only, tri-state (True delivered / False confirmed failure
(relay exit ≠ 0, ≠ 255; no ssh) / None unknown (ssh exit 255, timeout, other error));
escapes `$` as `USD `. Records + lock: settle_record.py. Target uuid:47bd6dd5-2c96-4514-9541-acd8ed013612.
Route: NIMROD -> SSH -> VPS -> openclaw message send -> Signal.
cron_runner_mac.sh: should_send() always returns 1 (never sends). Scripts send only
through signal_verify.

OpenClaw's own chat replies are model-written and NOT covered — see
skills/valedia/claude-bridge.md TODO(Burton).
