# Grok Build status

**Updated:** 2026-09-15 20:45 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 5: reconcile_run injected getter + rebalancer shadow Intents

**Last done:** Slice 5 on `valedia-v2-grok-build`

- A) `reconcile_run.py`: cron walks UNRECONCILED ledger rows via `spot_reconcile` with an **injected** getter. Shipped default is `mock_spot_order_getter` (returns None, no HTTP). Hook: `set_spot_order_getter(fn)` for Valedia runtime to supply a real GET later. Tests pass `kalshi=False` so this lane never opens a venue socket. Paper/sandbox/unreconciled still cannot lift Kelly.
- B) `rebalancer.py` proposed trades emit never-dispatchable shadow Intents (Slice 1 schema) via `emit_rebalancer_shadow`. Buy `quote_size` / sell `base_size` POST compare matches `coinbase_trader`. `market_buy`/`market_sell` are not imported and not called. Writer stays `valedia`. `VENUE_WRITER` not cut over.
- Tests: `test_reconcile_run.py` + `test_rebalancer_shadow.py` PASS (no network). Slice 1–4 still PASS.

**Shipped:**
- Slice 1 `e385c59` Intent seam
- Slice 2 `f9cc19e` allocator Coinbase shadow
- Slice 3 `f115029` fee-true spot fills / UNRECONCILED
- Slice 4 `f360513` GET-reconcile + brain shadow
- Slice 5 (this commit) reconcile_run wiring + rebalancer Intents

**Goal:** Max profit — UNRECONCILED stubs can be upgraded when Valedia injects a real GET; rebalancer drift is Intent-shaped before any auto-exec.

**Fence:** No --live, no orders, no key changes, no NT writer cutover, no `nautilus_trader`. Default reconcile getter never HTTP.

**Next:** Valedia-runtime GET injection (not this lane). Still no `VENUE_WRITER=nautilus`. No auto-exec rebalancer.
