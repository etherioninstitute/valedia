# Ponytail audit — INFORMED-BEFORE-CREATE (0.57.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no `POLYMARKET_US_LIVE`, no `VENUE_WRITER` edit, no wallets, no ACH, no halt bypass, no fee-map git True, no `NAUTILUS_LIVE`, no `TREASURY_AUTO`. Kalshi create tightened. Poly create stays blocked.

## Applied

`reuse:` `kalshi_fee_map.consult` / `allow` and `kalshi_profit.route` (same `ev_preflight` as `KALSHI_EV_PREFLIGHT_GATE`). No second fee formula.

`reuse:` `quant_engine.kelly_size` + `dynamic_per_trade_cap` (FTL track-record already inside the cap). Contract cap is `floor(bet/price)` so the helper's `max(1, …)` bump cannot waive size.

`reuse:` `live_exec_fenced` / `ticker_family_fenced` for oil `NO_HARVEST` and `LIVE_EXEC_FENCED`. `demote_blocks` only when `KALSHI_FILL_LEDGER_DEMOTE` is already on.

`yagni:` no new env that can turn the gate off. `INFORMED_BEFORE_CREATE` is const True. `POLY_TAG_PRIOR_CREATE` is const False. Snapshot source stays `const` even if those env names are set.

`yagni:` no caller rewrite that invents FV, depth, or bankroll. `create_order` / `sell_position` / US `create_order` refuse when the evidence dict is missing. Executor, allocator, sports, and latency already catch that and mark the attempt failed.

`yagni:` Poly paper propose path unchanged. `create_eligible` is a flag. `create_order` on the loop stays false. `tag_prior` can still be a DRY/SHADOW row.

## Findings

`ponytail:` one module, two call sites (Kalshi create/sell, Poly US create). Loop eligibility calls the same `create_eligible`.

`ponytail:` taker hurdle is the existing `route()` rule (taker net ≥ maker net + `KALSHI_TAKER_HURDLE`, default 0.02). Maker floor is net > 0.

`shrink:` do not plumb a fake `informed` dict through the scanners in this slice. Add it when a caller actually has model/rules-clock/researched FV, a confidence flag, book depth, and a bankroll.

**net:** one gate + tests + snapshot rows. 0 deps. 0 live flag flips. Lean enough. Ship.
