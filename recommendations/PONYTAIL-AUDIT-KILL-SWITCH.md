# Ponytail audit — KILL-SWITCH (0.39.0)

**Mode:** `/ponytail` full on the KILL-SWITCH diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_config.require_not_halted`, venue writers, allocator/rebalancer, CLI, `test_kill_switch.py`.
**Fence:** no live orders, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE/treasury flips, no crontab.

## Applied this session

`reuse:` `_env_flag` + `HaltedError` next to existing live-flag helpers. Poly still uses `LiveWriteBlocked` for `POLYMARKET_US_LIVE` off; halt is checked first in `_require_live`.

`yagni:` a second fence class per venue. One `require_not_halted`. **Not shipped.**

`yagni:` auto-Signal / auto-spend to clear. Ping file `send: false` only. **Not shipped.**

`yagni:` daemon that watches cockpit and flips halt. Hooks exist (`record_halt_event`, `panel_looks_sick`); report-only. **Not shipped.**

`yagni:` blocking Kalshi `cancel_order` / GET. Kill-switch is new orders. **Not shipped.**

`yagni:` editing `~/.openclaw/.env` or crontab so cron sees env. Halt JSON file instead. **Not shipped.**

## Findings

`shrink:` CLI is argv on/off/status over the same write helper. No extra framework.

**net:** 1 helper, 1 CLI, 1 test, 2 recs, 0 deps, 0 cron, 0 live flags. Lean enough. Ship.
