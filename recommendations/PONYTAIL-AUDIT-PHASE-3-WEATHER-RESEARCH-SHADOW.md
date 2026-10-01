# Ponytail audit — PHASE-3 weather RESEARCH/SHADOW (0.60.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** weather stays `LIVE_EXEC_FENCED`, mode `RESEARCH/SHADOW`, `dispatchable=0`. Oil stays `RESEARCH` / `LIVE_EXEC_FENCED`. No `create_order`. No Poly writer. No `POLYMARKET_US_LIVE=True` in git. Screens `ot-valedia-desk` (783) and `valedia-glance` (755) not signaled.

## Applied

`reuse:` `session_clocks.py` for `session_clock` / `resolve_clock` / `fence_gate`. The scorer calls them. It does not grow a second clock.

`reuse:` `kalshi_fee_map.fee` for the fee-true quote. Unknown city series (not in the artifact) pay taker M=1. Maker-free only when the row says `maker_viable`.

`reuse:` `KELLY_SAFETY_FRACTION` and `KELLY_BASE_CAP`. Shadow size does not call `dynamic_per_trade_cap` (that reads the live track-record files).

`yagni:` no import of `weather_arb` (that module loads the Kalshi client). Bracket math is the same Gaussian, inlined. No NOAA HTTP in the writer. Cache observe days are March 2026; attaching today's forecast would be a false fair value.

`yagni:` no new dependency. No cron row. No writer path. Screens not reloaded. The cockpit row is source `const`.

## Findings

`ponytail:` city zones are a fixed map of 14 cities. An unmapped code is `city_tz_unmapped`, not a guessed zone. Upgrade path is one row in `CITY_TZ` when the station zone is actually known.

`shrink:` the on-disk universe is the fee-map keys plus the city cache. Six series. The fixture row is labeled `book=fixture`.

**net:** one scorer module, a clock hook, one fence reason on city tickers. 0 deps. 0 live creates from this run. Lean enough. Ship.
