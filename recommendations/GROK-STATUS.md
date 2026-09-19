# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-19 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** OPENTERMINAL-VALEDIA SHIPPED. Next=staged ON (STOP for Burton — do not auto-flip live).

**Version:** `0.40.0` tag `valedia-v0.40.0` tip `3327adc`. Prior: `0.39.0` `4b7b6d3`.

**Tests:** `test_openterminal_valedia.py` + `test_h4.py` (version-bumped: slice23 / h3 / treasury / mirofish_smoke / poly_us / h2 / h5–h8 / nt1 / nt2 / poly_e / adversarial / valedia_full_smoke / kill_switch). No production ledger writes. No live flips.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-OPENTERMINAL-VALEDIA.md` (thin). No OpenTerminal Next fork. Glance on :8765.

**Fence:** No live orders, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False. `REBALANCER_AUTOEXEC` False. `VALEDIA_TREASURY_AUTO` False. `VALEDIA_TRADING_HALT` default False (unset = normal). FENCED. UI has no send/buy/create_order controls.

**Next:** staged ON — STOP for Burton. Do not auto-flip live.
