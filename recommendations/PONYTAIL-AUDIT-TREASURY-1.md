# Ponytail audit — TREASURY-1 report-only funding (0.36.0)

**Mode:** `/ponytail` full on the TREASURY-1 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_config` floors + `VALEDIA_TREASURY_AUTO`, `treasury_status.py`, `test_treasury.py`, Burton guide, rebalancer shadow note.
**Fence:** no ACH/wire/card, no wallet/venue transfer, no `--live`, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE flips. No keys in git.

## Applied this session

`yagni:` a transfer engine. Auto top-up is a named flag default False; `attempt_auto_topup` **raises** even if env=1. **Not shipped.**

`yagni:` webhook POSTs. `TREASURY_ALERT_WEBHOOK` is an unused stub; alerts are strings on JSON. **Not shipped.**

`yagni:` Mercury ACH / routing numbers / new bank client. Guide points at human ACH. Existing `fund_pipeline.py` left untouched (has live Mercury account digits — do not copy). **Not shipped.**

`yagni:` HL/Kraken cash reads (`kraken_client.get_balance` is a private POST). Writer none → DOCUMENT. **Not shipped.**

`yagni:` crontab for `treasury_status.py`. Run by hand. **Not shipped.**

`reuse:` `kalshi_auth.get_balance`, `polymarket_us_client.balances`, `coinbase_trader.get_balances`, `alpaca_executor.get_account`. Injectable readers so tests never hit the wire.

## Findings (biggest cut first)

`shrink:` no new venue SDK. One ~220-line report script + config knobs + mocks.

`yagni:` wiring treasury into `rebalancer.exec_rebalance_trades`. Docstring only. AUTOEXEC stays False.

`yagni:` env overrides per floor. Burton edits `TREASURY_FLOOR_*` in config if needed.

## Not cut (spec)

- Per-venue cash vs floors; WARN string if below.
- `VALEDIA_TREASURY_AUTO=False`; auto path refused in tests.
- Burton guide: first human fund, compound-in-place, when not to cross-venue, Poly US debit/ACH/wire pointer.
- Emit `models/treasury_status.json`.

**net:** 1 script, 1 test, 1 guide, config knobs, 0 deps, 0 cron, 0 transfers. Lean enough. Ship.
