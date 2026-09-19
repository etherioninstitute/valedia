# Ponytail audit — VALEDIA-FULL-SMOKE (0.38.0)

**Mode:** `/ponytail` full on the FULL-SMOKE diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `valedia_full_smoke.py`, `test_valedia_full_smoke.py`, `recommendations/VALEDIA-FULL-SMOKE.md`.
**Fence:** no live orders, no writer/AUTOEXEC/US_LIVE/SPOT/NT/SELF_ADJUST/AUTO_RAISE/treasury flips.

## Applied this session

`yagni:` a second fence checker. Reuse `mirofish_smoke.check_fence` + writer/version stamps. **Not shipped.**

`yagni:` reimplementing treasury/poly/mirofish/cockpit/pnl. Call `collect_status`, `poly_us_smoke.run_smoke`, `mf.run_smoke`, `cockpit.panel`, `pnl.load_pnl`. **Not shipped.**

`yagni:` crontab for the smoke. Burton runs it by hand. **Not shipped.**

`yagni:` kill-switch (`VALEDIA_TRADING_HALT`). Next queue. **Not shipped.**

`reuse:` `test_poly_us.MockXport` in the offline test (no second mock).

## Findings

`yagni:` wrapping Kalshi in a new client. `kalshi_auth.get_balance` + `socket.setdefaulttimeout(15)` because `kalshi_auth.get` has no timeout. # ponytail: add timeout= on kalshi_auth.get when that module is touched.

`yagni:` cockpit HTTP server start. `panel()` is enough; GET :8765 SKIP if down.

`shrink:` one CLI, one test, two recs. Version bump because harness code shipped.

**net:** 1 script, 1 test, 2 recs, 0 deps, 0 cron, 0 live flags. Lean enough. Ship.
