# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-19 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** KILL-SWITCH SHIPPED. Next=OPENTERMINAL-VALEDIA (do not auto-start this session — STOP for parent chain).

**Version:** `0.39.0` tag `valedia-v0.39.0` tip `4b7b6d3`. Prior: `0.38.0` `cdbec1b`.

**Tests:** `test_kill_switch.py` PASS offline. Version-bumped: slice23 / h3 / h4 / treasury / mirofish_smoke / poly_us / h2 / h5–h8 / nt1 / nt2 / poly_e / adversarial / valedia_full_smoke. No production ledger writes. No live flips.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-KILL-SWITCH.md` (thin). One `require_not_halted` helper. No second fence, no Signal send, no crontab.

**Fence:** No live orders, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False. `REBALANCER_AUTOEXEC` False. `VALEDIA_TREASURY_AUTO` False. `VALEDIA_TRADING_HALT` default False (unset = normal). FENCED.

**Next:** OPENTERMINAL-VALEDIA — parent launches. Do not auto-start. Do not flip staged ON.
