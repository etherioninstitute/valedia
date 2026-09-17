# Grok Build status

**Updated:** 2026-09-17 14:40 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 7: spot GET helpers OFF + per-strategy cap + stagger allocator vs sports crons

**Last done:** Slice 7 on `valedia-v2-grok-build`

- A) Spot GET helpers: `coinbase_trader.get_order` + `alpaca_executor.get_order` + `spot_order_get.{coinbase_order_get,alpaca_order_get,live_spot_order_getter}`. Default cron still `mock_spot_order_getter` (no HTTP). `valedia_config.SPOT_RECONCILE_HTTP = False`. Flag-off helpers return None even if called. Tests inject `_request`/`_get` only.
- B) **Single flip (Burton/Valedia later, not this lane):** `export SPOT_RECONCILE_HTTP=1` (or `valedia_config.SPOT_RECONCILE_HTTP = True`). Then `reconcile_run.get_spot_order_getter()` returns `live_spot_order_getter` (read-only GET). Do not enable from this lane.
- C) Per-strategy cap: `category_track_record_mult` from fee-true `category_stats` (thin → 0.5, loser → 0.3, winner toward 1.0 within fuse). Wired into `dynamic_per_trade_cap(category=...)`, `sports_engine.size_bets` (SPORTS), `master_allocator.allocate` (per `type`), `KellySizer.size_position`. Paper/sandbox/unreconciled/backfill cannot lift. A hot other category cannot lift this one.
- D) Cron stagger: sports `0 */2` (:00 every 2h), allocator `20 */4` (:20 every 4h) in `install_cron.sh` + `cron-schedule.md`. Offset = 20 min (`SPORTS_CRON_MINUTE=0`, `ALLOCATOR_CRON_MINUTE=20`). Worktree config only — live crontab is unchanged until Burton installs/merges.
- Tests: `test_slice7.py` + Slice 3–6 suite PASS (no network). `SPOT_RECONCILE_HTTP` False ⇒ no HTTP.

**Shipped:**
- Slice 1 `e385c59` Intent seam
- Slice 2 `f9cc19e` allocator Coinbase shadow
- Slice 3 `f115029` fee-true spot fills / UNRECONCILED
- Slice 4 `f360513` GET-reconcile + brain shadow
- Slice 5 `c3086df` reconcile_run wiring + rebalancer Intents
- Slice 6 `659d4b7` Kalshi fill-true + sports cap + one SPORTS category + backfill exclude
- Slice 7 `7e7f8ef` spot GET off + per-strategy cap + cron stagger

**Goal:** Max fee-true realized P&L. Crypto/equity *can* enter the ledger when Burton flips GET; winners are not starved by graves; sports and allocator cannot double-bet the same minute.

**Fence:** No --live, no orders, no key changes, no NT writer cutover, no `nautilus_trader`, **GET HTTP remains off**. Tests mocked only.

**Next:** Slice 8 — one fusion + one fee table; scalper/weather dead; `REBALANCER_AUTOEXEC=False`. Do not enable GET HTTP. Do not flip `VENUE_WRITER`.
