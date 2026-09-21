# Ponytail audit — POLY-LOOP-UNIVERSE (0.52.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** `POLYMARKET_US_LIVE` stays False. Writer polymarket `none`. No weather/oil/CB/Alpaca/treasury/nautilus live. No crontab. No wallets.

## Applied

`reuse:` existing `list_markets(**extra)` (no client rewrite). `poly_learn._load_json` / `cheap_tags`. Same skip/size/ledger path.

`yagni:` `modeled_prob` (crypto series, not poly). Ollama/TypeSafe FV. Second writer. New HTTP host.

`yagni:` extra query encoder for `orderBy[]`. `closed=false` + client-side filter is the drop.

## Findings

`ponytail:` `endDateMin` makes gateway ignore `closed=false` (stale NFL). Dropped that param. Missing US `liquidity` is not treated as 0. `skip_reasons` is the desk readout.

**net:** one module + tests + doc. 0 deps. 0 live flag flips. Lean enough. Ship.
