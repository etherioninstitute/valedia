# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-18 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** STOP — POLY-US-2 SHIPPED (retail whoami + preview dry-run). Prior: POLY-US 0.34.0.

**Version:** `0.35.0` tag `valedia-v0.35.0` (tip after code commit). Prior: POLY-US `0.34.0`.

**Tests:** `test_poly_us.py` PASS (mocked + live-http preview). `poly_us_smoke.py` PASS (create blocked). `test_poly_e.py` / `test_h8.py` / `test_h7.py` / `test_h5.py` / `test_h3.py` / `test_slice23.py` PASS.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-POLY-US-2.md`

**POLY-US-2:** `whoami()` / `whoami_retail()` = `GET /v1/account/balances` (redacted). Does **not** call institutional `GET /v1/whoami` (`api.prod.polymarketexchange.com` bearer JWT; 404 on `api.polymarket.us`). `preview_order` wraps `{request: order}` (`POST /v1/order/preview`). Smoke: `scripts/integrations/poly_us_smoke.py` — HTTP=1, LIVE=0, qty=1 tiny limit, `create_order` raises `LiveWriteBlocked`. Matrix: `POLYMARKET-US-API-MATRIX.md`. Cutover: `POLYMARKET-US-CUTOVER.md` — **do not flip**.

**Fence:** No `--live`, no venue orders, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False.

**Next:** STOP.
