# Ponytail audit — KALSHI-FEE-MAP (0.45.0)

**Mode:** `/ponytail` full on the KALSHI-FEE-MAP diff only.
**Fence:** no POLYMARKET_US_LIVE, no VENUE_WRITER restore, no weather/oil unfence, no live order, no halt bypass.

## Applied

`yagni:` new client class. Three GET wrappers on existing `kalshi_auth.get`.

`yagni:` scrape all 142 PDF non-standard rows. First page only (14); API `fee_changes` overlays.

`yagni:` blocking gate default on. `KALSHI_FEE_MAP_GATE=False`; consult annotates. Drop only if Burton flips.

`yagni:` rewrite `fee_engine` / FTL rounding. Additive `fee(P,C,M)`; live `kalshi_fee` untouched.

## Findings

`shrink:` sports/NFL PDF pages still garbled. `refresh(get_series_fee_changes)` fills those when GET is allowed.

`yagni:` event-ticker consult path exists in `build_map`; sports still keys series not event. Add when an event override actually bites.

**net:** one module + 3 GETs + 3 consult hooks. 0 deps. 0 live flag flips. Lean enough. Ship.
