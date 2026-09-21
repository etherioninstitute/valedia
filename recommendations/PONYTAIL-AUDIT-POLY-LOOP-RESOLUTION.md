# Ponytail audit — POLY-LOOP-RESOLUTION (0.53.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** `POLYMARKET_US_LIVE` stays False. Writer polymarket `none`. No weather/oil/CB/Alpaca/treasury/nautilus live. No crontab. No wallets.

## Applied

`reuse:` existing `resolution_source` / `_named` / nested `event`/`market`/`metadata` walk. Same skip/size/ledger path. FV still `_cache_fv` only.

`yagni:` NLP / Ollama / TypeSafe source extraction. Event GET extra round-trip. New HTTP host. Invented sources ("governing league").

`yagni:` flipping `no_resolution` into a fake FV. Description-only still skips `no_fv` until cache/`fairValue`.

## Findings

`ponytail:` US docs list `description`, not `resolutionSource`. Known-label regex + URL host, else `rules_text:<hash>:<snippet>`. Fail closed only on zero text. Add a label row when a live skip names a source we miss.

**net:** one helper + tests + doc. 0 deps. 0 live flag flips. Lean enough. Ship.
