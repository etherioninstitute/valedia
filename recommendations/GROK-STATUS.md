# Grok Build status

**Updated:** 2026-09-17 22:15 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 9 / P1.4 (`valedia-v2-grok-build`)

**Shipped:** Slices 1–9 (`e385c59` … Slice 8 `02987c3` + Slice 9 `a0f3572`)

## Slice 9 / P1.4 (learning pause, not a grave)

**Rule (in code + here):** `strategy_blacklist.json` is a pause, not a life sentence.

1. **pnl_tracker is report-only for Kelly.** WR `<25%` after 3 trades is `wr_report` only (`fuse: report_only`). It does not fight the fee-true category gate. Fat-tail winners (low WR, net>0) stay eligible.
2. **Pause on NET.** Prefer `category_stats` (fee-true). Fallback `pnl_tracker` profit−cost. Need `n >= CATEGORY_MIN_TRADES` (4) and `net < 0`. Never raw WR.
3. **Re-enable:** `PAUSE_TTL_DAYS=7` then probation (eligible for evaluation/sizing; will not immediately re-pause). Evidence `n>=4` and `net>=0` unpauses immediately. Legacy `blacklisted[]` with no `until` is expired on read (a grave is invalid).
4. **Allocator / category gate:** gather+allocate skip *paused until …*. Unpaused weather/oil may be sized/gated. Execute: `SKIP_PAUSED` vs `SKIP_LIVE_FENCE`. `should_trade_category` says `paused until ISO (learning TTL; not permanent)`; retest override still wins. 45d net gate still ages out.

**Slice 8 live exec still dead:** `WEATHER_LIVE=False`, `SCALPER_LIVE=False`, `weather_arb` never `create_order`, scalper never `alp_buy`. Unpause ≠ live-buy. `SPOT_RECONCILE_HTTP=False`, `REBALANCER_AUTOEXEC=False`, `VENUE_WRITER` unchanged, no NT.

**Tests:** `test_slice9.py` + `test_slice8.py` + `test_category_gate.py` + prior slices PASS. Drift detector 10 PASS. No network.

**Burton policy honored:** weather/oil re-enter *evaluation* when TTL/evidence says so. Live writers stay fenced.

**Next:** STOP for Burton. Do not flip AUTOEXEC / GET HTTP / VENUE_WRITER / --live. Optional P2.1 NT venv only if Burton says go.

**Fence:** No --live orders, no SPOT_RECONCILE_HTTP=True, no VENUE_WRITER flip, no NT install. REBALANCER_AUTOEXEC stays False.
