# Ponytail audit — PHASE-2 session / resolve clocks (0.59.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** oil/weather stay `LIVE_EXEC_FENCED`. No mass `create_order`. No Poly writer. No `POLYMARKET_US_LIVE=True` in git. Screens `ot-valedia-desk` / `valedia-glance` not signaled. No Phase 3 launch.

## Applied

`reuse:` one module, `session_clocks.py`. Informed and the allocator call it. No second OMS, no clock class tree.

`reuse:` `live_exec_fenced` / `weather_live_enabled`. The clock fence reads them. It does not grow the oil series set and it does not clear `WEATHER_ARB` / `OIL_BRACKET`.

`reuse:` informed-before-create remains the create refuse. New reasons are `session_inactive`, `resolve_unknown`, `clock_stub`. Callers still do not invent a fair value.

`yagni:` no env that turns the clocks off. No HTTP poll inside `next_window` (the caller passes markets it already fetched). No MVE RFQ client. No free-text observe-day parser (Phase 3). Paper `create_eligible` stays the FV bar; live Poly create is `poly_reasons`.

`yagni:` screens not reloaded. Snapshot rows exist. The running cockpit process was left alone.

## Findings

`ponytail:` 15m resolve uses `close_time` because that series *is* the window. Sports and Poly do not. Upgrade path is a per-series rule if a 15m contract starts settling on a later timer.

`shrink:` arb and MVE are a skip stub (`SKIP_CLOCK_STUB` / `no_live_rfq`), not a new executor.

**net:** one module plus a gate call. 0 deps. 0 live creates from this run. Lean enough. Ship.
