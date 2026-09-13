---
description: >
  Signal messaging protocol. Exactly two message types.
  Everything else goes to local logs.
---

# signal protocol

## Rule

Burton receives exactly two types of Signal messages from Valedia:
1. Money invested (bets placed)
2. Profit realized (bets settled)

ALL other output goes to: ~/.openclaw/logs/valedia-cron.log

## Message format

### Invested (sent by sports_engine.py)
```
Invested: 281.46 on 12 games
```
Triggered: when sports_engine.py executes real orders (not dry-run)
Source: signal_send.py

### Profit (sent by settle_run.py)
```
Profit: +4.96 (1W/0L settled)
Total: +6.06 (7W/1L)
```
Triggered: when settle_run.py detects new settlements on Kalshi
Source: signal_send.py
Silent when nothing settled.

## Implementation

signal_send.py: shared helper, escapes dollar signs (replaced with "USD ")
Target: uuid:47bd6dd5-2c96-4514-9541-acd8ed013612
Route: NIMROD -> SSH -> VPS -> openclaw message send -> Signal

cron_runner_mac.sh: should_send() always returns 1 (never sends).
All 24 cron jobs log to disk only. Scripts handle their own alerts.

## Dollar sign escaping

Bash eats dollar signs through SSH. All messages use sed 's/\$/USD /g'
before sending. "4.96" not "$4.96" in Signal messages.
