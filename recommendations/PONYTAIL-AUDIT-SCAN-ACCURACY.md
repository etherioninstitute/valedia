# Ponytail audit — SCAN-ACCURACY (0.42.0)

**Mode:** `/ponytail` full on the SCAN-ACCURACY diff only.
**Fence:** no POLYMARKET_US_LIVE, no VENUE_WRITER restore, no weather/oil unfence.

## Applied

`yagni:` extra-capital deposit block. Averaging `edge/(price+0.01)` invented 100%+ ROI. Deleted.

`yagni:` second fence API. One `live_exec_fenced(name, ticker=)` + series prefixes. Callers pass ticker.

`yagni:` mixed-book deployable. Per-venue fuse; Kalshi-only ignores CB/Alpaca equity.

## Findings

`shrink:` report still lists fenced oil as FENCED (ears on). Do not unfence.

**net:** three helpers + tests + Accuracy bar. 0 deps. 0 live flag flips. Lean enough. Ship.
