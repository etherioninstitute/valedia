# Ponytail audit — POLY-LEARN (0.41.0)

**Mode:** `/ponytail` full on the POLY-LEARN diff only.
**Fence:** `POLYMARKET_US_LIVE` stays False. No writer flip. No weather/oil/CB/Alpaca live.

## Applied

`yagni:` settlement / price-history / WS / event-by-slug. Sizing needs slug + BBO (book fallback).

`yagni:` mixing Poly opps into live `gather_all_opportunities`. Sibling `poly_learn.py` + `run()` line + execute `DRY/SHADOW` guard.

`yagni:` Ollama on every event. Cached MiroFish/persona JSON + nav classify.

`yagni:` flipping LIVE. Proposals file + smoke still `LiveWriteBlocked`.

## Findings

`shrink:` `sizing_from_bbo` one helper for BBO and book.

`yagni:` second allocator. Classroom paper $5 notional, treasury floor as cap.

**net:** client helpers + ~170-line sibling + tests + two docs. 0 deps. 0 live flips. Lean enough. Ship.
