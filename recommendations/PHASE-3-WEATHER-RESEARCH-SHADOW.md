# PHASE-3 — Weather RESEARCH / shadow (live-fenced)

**Burton 2026-09-22 ~02:20 PT.** Queue after Phase 2. Explicit: RESEARCH + shadow only — **do not unfence** weather live create.

**NORTH STAR:** informed → fee-true EV → size → FTL. MiroFish seasons only. Official NOAA/NWS/print sources beat vision hype.

## Goal
Build a weather desk that can **score would-have trades** and parse clocks — without `create_order` on weather series until Burton un-fences later.

## Scope (do)
1. **Universe:** Kalshi weather series already in fee-map / live_data (city contracts). Inventory tickers + observe-day / Last Trading Time text patterns.
2. **Clocks:** Use Phase 2 `session_clock` / `resolve_clock` / `fence_gate` — weather stays `LIVE_EXEC_FENCED`. Parse free-text early_close / observe-day into typed fields where possible; document city-TZ gaps honestly.
3. **FV sources (official first):** NOAA/NWS / Climate Data / published forecasts — never Muse-satellite-alone as FV. Optional MiroFish narrative season only; refuse default-50 as create fuel (shadow score may tag `season_only`).
4. **Shadow ledger:** For each candidate: enter window, model FV, fee-true EV, would-have size, would-have P&L vs eventual settlement — write `models/weather_shadow_ledger.json` (or equivalent). `dispatchable=0` / no live writer path.
5. **Controls honesty:** cockpit/controls show weather as RESEARCH/SHADOW + fenced; never imply live-eligible.
6. Tests with fixtures (observe-day text, missing TZ). Ponytail audit + GROK-STATUS.

## Explicit non-goals
- Unfence weather or oil live
- Muse/GDELT multimodal as required FV
- Mass or any weather `create_order`
- CB/Alpaca Ag/energy live from this slice
- Paid Benzinga/Polygon as mandatory

## Hard fence
- Weather create remains refuse / LIVE_EXEC_FENCED
- Do not kill `ot-valedia-desk` / `valedia-glance`
- Do not flip POLYMARKET_US_LIVE / writers / wallets
- Next=Burton when done (stop chain)

## Tools
RuFlo MCP `http://127.0.0.1:8810/mcp` filtered. CBM. Ponytail after code.

## Deliverables
1. Shadow scorer + ledger artifact + clock parsing hooks
2. `PONYTAIL-AUDIT-PHASE-3-WEATHER-RESEARCH-SHADOW.md`
3. Results appended here + GROK-STATUS; **Next=Burton**

## Done when
Weather candidates can be shadow-scored with fee-true EV and honest clocks/fence; zero live weather creates possible from this path; audit written.

## Results (2026-09-22 PT)

**Status:** DONE. **Next = Burton.** Stop the chain. Do not unfence weather or oil.

Version `0.60.0` (git constant bumped; not committed, not tagged). Tip before this slice `10b24aa`.

`scripts/integrations/weather_shadow.py` scores would-have rows and writes `models/weather_shadow_ledger.json`. `dispatchable=0`. No Kalshi client import. No `create_order`. No NOAA pull on the writer (cache observe days are 2026-03-26; today's forecast is not their fair value).

Universe on disk, 6 series, no live book, size 0, tag `no_fv`:

- Fee-map: `KXTEMP`, `KXHURRICANE`. No city code (`city_absent`). No observe date. No Last Trading Time text in the artifact.
- City cache: `KXHIGHNY`, `KXHIGHCHI`, `KXHIGHDEN`, `KXHIGHAUS`. Observe date `2026-03-26` from the title. Last Trading Time absent (`ltt_missing`). City zones are mapped (NY, CHI, DEN, AUS).

Worked example in `scored[]` is `book=fixture`, not a live book. NWS forecast 80° vs `>72°`, fv 0.99, price 0.40. `KXHIGHNY` is not in the fee-map, so the quote is fail-closed taker. Size 20. Fee-true EV 11.46. Settlement yes would-have P&L 11.66. Session `active=false`, reason `LIVE_EXEC_FENCED`. Observe day `2026-09-22`. Last Trading Time `11:59 PM ET` → `America/New_York`.

Clock hook stays on `session_clock` / `resolve_clock` / `fence_gate`. Weather mode is `RESEARCH/SHADOW`, `live_exec=false`, `dispatchable=0`. Oil mode stays `RESEARCH`. An observe date is not a resolve instant (`observe_date_not_instant`). A clock with no zone is not stamped UTC (`ltt_tz_missing`, `city_tz_unmapped`). `CITY_TZ` is the cities we actually know. Other city codes stay gaps.

`informed_before_create.kalshi_reasons` adds `live_fence` for weather series tickers (`KXHIGH` / `KXLOW` / `KXRAIN` / `KXSNOW` / `KXTEMP` / `KXWIND` / `KXWEATH` / `KXHURRICANE`) even when the strategy is not `WEATHER_ARB`. Allocator `before_create` returns `SKIP_LIVE_FENCE`. `WEATHER_LIVE` stays hard-off. Oil series fence unchanged.

Controls row `WEATHER_SHADOW`: `RESEARCH/SHADOW | fence=LIVE_EXEC_FENCED | dispatchable=0`. Source `const`. Running `ot-valedia-desk` (screen 783) and `valedia-glance` (screen 755) were not signaled, so the live panel is still the previous process.

Tests PASS: `test_weather_shadow.py`, `test_session_clocks.py`, controls, informed, slice8 (fee-map gate held off so `weather_arb`'s env load does not re-arm it), kill switch, orders v2, profit, fee-map, poly US. No live create.

Audit: `PONYTAIL-AUDIT-PHASE-3-WEATHER-RESEARCH-SHADOW.md`.
