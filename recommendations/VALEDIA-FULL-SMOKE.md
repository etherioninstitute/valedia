# VALEDIA-FULL-SMOKE

**Updated:** 2026-09-22 PT
**Harness:** `python3 scripts/integrations/valedia_full_smoke.py`
**Version:** `0.57.0` stamp `v0.57.0 4708fd5*`
**Verdict:** **PASS**
**Wall:** 41.004s / 600s

Hard fence held: `POLYMARKET_US_LIVE` reported honestly (env-only). No `VENUE_WRITER` changes, no `REBALANCER_AUTOEXEC`, no `NAUTILUS_LIVE`, no `VALEDIA_SELF_ADJUST`/`MIROFISH_AUTO_RAISE`, no treasury auto, no mass `create_order`, no ACH, no oil/weather unfence.

| # | Section | Result | Reason |
|---|---------|--------|--------|
| 1 | fence (critical) | **PASS** | v0.57.0 writers held, POLYMARKET_US_LIVE on |
| 2 | treasury (critical) | **PASS** | report-only; auto refused; no ACH |
| 3 | mirofish (critical) | **PASS** | fixture PASS; live-lite PASS; bionic=PASS; backend=PASS; neo4j=PASS |
| 4 | kalshi | **PASS** | GET /portfolio/balance + fee-map consult |
| 5 | poly_us | **PASS** | whoami_retail/balances + preview; LIVE on; create skipped |
| 6 | shadow_adversarial (critical) | **PASS** | queue intents=337 dispatchable=0; adversarial paper import ok |
| 7 | cockpit (critical) | **PASS** | panel() LIVE; GET PASS |
| 8 | pnl (critical) | **PASS** | broker summary present; label broker≠FTL; FTL n=1 |

**Exit:** `0` (nonzero only on critical fence/fixture FAIL; optional live SKIP is not a fail).

## Not run

- live orders / Kalshi writes / Poly create (blocked)
- ACH / wire / card / treasury auto top-up
- kill-switch: `valedia_config.require_not_halted` (see `KILL-SWITCH.md`)
- live OpenTerminal market UI (:3000) — Valedia glance is :8765 (opt-in --listen)

**Next = Burton.** Do not auto-start another slice.
