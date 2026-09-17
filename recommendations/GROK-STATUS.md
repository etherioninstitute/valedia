# Grok Build status

**Updated:** 2026-09-17 15:24 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 10: MiroFish/news intel → opportunities → gated Intents (`valedia-v2-grok-build`)

**Shipped:** Slices 1–10 (Slice 9 `a0f3572` + Slice 10 `b5600e0`)

## Slice 10 (intel → opportunity → gated Intents)

1. **Gatherer** `intel_opportunity_gatherer.py` reads local JSON only (MiroFish, fusion, hot_list new-listings, hedge intel, Kalshi scanner, latency-arb log). No HTTP. No keys. No ledger writes.
2. **Emits Slice-1 Intents** for crypto listed, crypto new-listing candidates, Alpaca stocks, Kalshi markets, arb *candidates*. `extra.shadow=True`, `exec_dry_run=True`, `live_flag=False`, `ledger_source=paper`.
3. **Gates:** fee-true (`use_static=True`; Coinbase/Alpaca round-trip), `should_trade_category`, learning-pause (`blacklist_ok=False` while paused), ERE HALT/DANGER. MiroFish blend capped at `MIROFISH_WEIGHT` (10%) when fusion is present.
4. **Kelly:** paper/sandbox/unreconciled/backfill/**shadow** cannot lift `real_track_record` / `category_stats` / `_track_record_mult`. Intel opps are **not** mixed into live `gather_all_opportunities`. Allocator `run()` fail-open shadow-emits only.
5. **Fence:** `INTEL_SHADOW_INTENTS=True` (never dispatchable). `NAUTILUS_ENABLED=False`, `VENUE_WRITER` unchanged, `SPOT_RECONCILE_HTTP=False`, `REBALANCER_AUTOEXEC=False`. NT flags flipped in tests still cannot dispatch these Intents.

**Tests:** `test_slice10.py` + `test_slice9.py` + `test_slice8.py` + `test_slice7.py` + `test_nautilus_intent.py` + `test_category_gate.py` PASS. No network.

**Next:** Slice 11 — Venue expansion shadow (HL + Polymarket). Do not start 11 until this ships. No --live, no SPOT_RECONCILE_HTTP, no VENUE_WRITER, no REBALANCER_AUTOEXEC, no NT install.

**Fence:** No --live orders, no SPOT_RECONCILE_HTTP=True, no VENUE_WRITER flip, no NT install. REBALANCER_AUTOEXEC stays False. Shadow/gated Intents only.
