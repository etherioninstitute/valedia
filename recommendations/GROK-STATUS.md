# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-18 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** POLY-US SHIPPED. Fence held. Next=STOP. Do not enable live Poly.

**Version:** `0.34.0` tag `valedia-v0.34.0` tip `e118ff5`. Prior: H8+POLY-E `0.33.0`.

**Tests:** `test_poly_us.py` PASS (mocked). `test_poly_e.py` PASS. `test_h8.py` / `test_h7.py` / `test_h5.py` / `test_h3.py` / `test_slice23.py` PASS.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-POLY-US.md`

**POLY-US:** `polymarket_us_client.py` — US DCM (`api.polymarket.us` + `gateway.polymarket.us`), env `POLYMARKET_KEY_ID`/`POLYMARKET_SECRET_KEY`. create/cancel gated `POLYMARKET_US_LIVE` default False. Nav Sports/Politics/Crypto/Weather/Tech/Culture/Econ via US tags. Fill-true: preview ≠ create; create ack ≠ fill. Matrix: `POLYMARKET-US-API-MATRIX.md`. Cutover: `POLYMARKET-US-CUTOVER.md` — **do not flip**. Int'l Gamma/CLOB (`polymarket_client.py`) unchanged. No `polymarket-us` SDK install.

**Fence:** No `--live`, no venue orders, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False.

**Next:** STOP. Do not enable live Polymarket.
