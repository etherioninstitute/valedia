# Grok Build status

**Updated:** 2026-09-16 23:40 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 6: Kalshi fill-before-EXECUTED + sports `dynamic_per_trade_cap` + SPORTS_TONIGHT→SPORTS + backfill exclude

**Last done:** Slice 6 on `valedia-v2-grok-build`

- A) Kalshi taker fill-true: `sports_engine.execute_bets`, `master_allocator` Kalshi taker, and `kalshi_executor.execute_trade` no longer stamp `EXECUTED` + `record_entry(source=live)` on a bare POST-ack. `extract_kalshi_fill` / `book_kalshi_fill` record live only when fill count > 0 (order.fill_count, executed+remaining=0, or get_fills rows for that order_id). Else `UNRECONCILED` with `order_id`. Maker path still records on get_fills.
- B) Sports sizing: `size_bets` uses `min(edge fraction, dynamic_per_trade_cap, 4% fund max)`. Retracted 86%/100% WR is not a sizing input (`LEAGUE_BONUS` gone; `get_optimal_bet_pct` no longer defaults to 86% or 10%).
- C) One sports bucket: `kalshi_executor` strategy `"SPORTS_TONIGHT"` → `"SPORTS"`. `CATEGORY_ALIASES` folds leftover `SPORTS_TONIGHT` into `SPORTS` for `category_stats` / `should_trade_category` / park+retest. Two crons cannot split Kelly or dodge the sports gate.
- D) `source` containing `backfill` (e.g. `backfill:pnl_tracker`) is `_is_non_real_source` — cannot lift `real_track_record` / Thorp opens / the 20-trade proven gate.
- Tests: `test_kalshi_fill_ledger.py` + sports B1 + category-gate + Slice 1–5 tests PASS (no network). Mocked ack-without-fill does not increment Kelly/open; fill payload does.

**Shipped:**
- Slice 1 `e385c59` Intent seam
- Slice 2 `f9cc19e` allocator Coinbase shadow
- Slice 3 `f115029` fee-true spot fills / UNRECONCILED
- Slice 4 `f360513` GET-reconcile + brain shadow
- Slice 5 `c3086df` reconcile_run wiring + rebalancer Intents
- Slice 6 `659d4b7` Kalshi fill-true + sports cap + one SPORTS category + backfill exclude

**Goal:** Max fee-true realized P&L. Kalshi taker no longer Thorp-throttles the book on unfilled acks or inflates Kelly; sports cannot 10% a retracted WR.

**Fence:** No --live, no orders, no key changes, no NT writer cutover, no `nautilus_trader`. Tests mocked only.

**Next:** Slice 7 — spot GET helpers shipped off (`SPOT_RECONCILE_HTTP=False`) + per-strategy cap from `category_stats` + stagger `allocator_run` vs `sports_run`. Cron minute change can ship there. Do not enable GET HTTP from this lane.
