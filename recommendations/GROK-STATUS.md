# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-19 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** KALSHI-FEE-MAP SHIPPED. Next=Burton (flip `KALSHI_FEE_MAP_GATE=1` only after a GET refresh populates sports multipliers).

**Version:** `0.45.0` tag `valedia-v0.45.0` (stamp after commit). Prior: `0.44.0` `39dfe68`.

**Tests:** `test_kalshi_fee_map.py` PASS (PDF Jul 7 2026 table + maker formula + mocked V2 GETs + gate off/on). `test_kalshi_orders_v2.py` PASS. `test_slice23.py` PASS. `test_sports_engine.py` PASS.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-KALSHI-FEE-MAP.md`. No new client; gate default off.

**Tool use:** RuFlo `hooks_route` (grok-build, 0.7) / `hooks_pre-task` / `memory_search` (kalshi-orders-v2). CBM `list_projects` + `get_architecture` scripts/integrations + `search_graph` fee/kalshi/allocator + `get_code_snippet` + `trace_path` `kalshi_auth.get` inbound + `check_index_coverage`. Ponytail after code (no PDF scrape of garbled pages, no FTL rewrite, no gate default on).

**Fence:** No live orders. No ACH/wire/card. No `POLYMARKET_US_LIVE`. No CB/Alpaca writer restore. No weather/oil unfence. No `VALEDIA_TRADING_HALT` bypass. `KALSHI_FEE_MAP_GATE` stays False. FENCED.

**Next:** Burton. Do not auto-flip `KALSHI_FEE_MAP_GATE`. Do not auto-flip `POLYMARKET_US_LIVE`. Do not unfence weather/oil.
