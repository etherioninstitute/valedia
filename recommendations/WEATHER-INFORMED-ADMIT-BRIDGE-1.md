# WEATHER-INFORMED-ADMIT-BRIDGE-1

**Owner:** Grok Build (opt-in) via Valedia Trader charter
**Goal:** Bridge NWS-settled official weather alerts into INFORMED so first-drop EV can admit without mass creates.
**Fence:** Oil stays fenced. No wallet/spend from Build. WEATHER_LIVE owned by Valedia runtime only. Max 1 admit/create attempt per run in proof.

## Problem (verified 2026-09-25)
Weather morning-go prices and alerts, but admitted=0 / create_order=false.
Live ledger attempts (KXLOWTDAL-26SEP24-B76.5, KXHIGHDEN-26SEP25-T72) both refused with:
`fv_source`, `fv_confidence`, `edge_missing`, `depth`, `size`.
Root: informed_from_opp / kalshi_reasons expect INFORMED keys; shadow rows have fee_true_ev + nws_cli settlement confidence but do not map into edge/fv_confidence/depth/bankroll. ALLOWED_FV lacks nws_cli/nws.

## Acceptance criteria
1. For NWS-settled official alerts only, build INFORMED payload that clears today's five refuses: allow or remap nws_cli/nws into create-path FV policy; set truthy fv_confidence from settlement confidence in {final,observation}; set edge from scored economics (derive from fee_true_ev / fv−price); set depth ≥ MIN_DEPTH; set bankroll + Kelly-valid count (cap small, e.g. 1–min(kelly, would_have_size)).
2. Populate bid/ask/slip from live quote so clearing edge_missing does not immediately hit spread_missing/slip_missing.
3. Shadow / --dry-decide / morning_scan remain create_order=false; only weather_live.admit may set true.
4. Proof: stubbed post= (no real Kalshi POST) on one alert like KXHIGHDEN-26SEP25-T72 (or next evening CLI-final alert) returns create_order=True OR live ledger reasons=[]; document before→after reason lists.
5. Do not loosen LOTTERY_PX=0.05, LATE_PX=0.90, or INFORMED fail-closed; oil OIL_BRACKET / KXWTI/KXOIL stay fenced; no mass create — admit only _alertable ∩ _nws_settled, hard max creates/run = 1.
6. Update WEATHER-MORNING-DIAGNOSIS / LAST JSON to report admit reason-code counts (not only shadow tags).
7. Promote only the bridge + tests to live ~/.openclaw/workspace; leave Valedia as WEATHER_LIVE owner; Build does not flip LIVE or unfence oil.

## Preferred change surface
Prefer mapping in weather_live.admit / a small weather→informed helper over widening global ALLOWED_FV for all desks — unless needed and narrowly scoped to NWS weather admit path.

## Key files
- ~/.openclaw/workspace/scripts/integrations/weather_live.py (admit)
- informed_before_create.py (ALLOWED_FV, kalshi_reasons, informed_from_opp)
- weather_morning_go.py, weather_shadow.py
- models/weather_live_ledger.jsonl

## Out of scope
Oil unfence, lottery/late relaxation, mass creates, paper desks, Grok flipping WEATHER_LIVE.

## Done when
GROK_EXIT:0 with stubbed-admit proof showing empty reasons (or create_order True under stub), diagnosis shows admit reason counts, live promote of bridge only.
